"""KG Extractor — local entity/relation extraction from parsed text.

Architecture:
  1. Text preprocessing: fix concatenated words, remove citations/URLs
  2. Entity extraction: regex + dictionary + jieba TextRank
  3. Entity validation: length/format/blacklist checks
  4. Relation extraction: pattern matching + co-occurrence
  5. Deduplication: case-insensitive merge with source tracking
"""
from __future__ import annotations
import re
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable
from hashmm.utils import get_logger

logger = get_logger("hashmm.kg.extractor")


# ── Data classes ──

@dataclass
class Entity:
    name: str
    entity_type: str
    description: str = ""
    source_ids: list[str] = field(default_factory=list)
    weight: float = 1.0
    modality: str = "text"   # V104 多模态: text | table | image

    @property
    def id(self) -> str:
        return hashlib.md5(f"{self.name}::{self.entity_type}".encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "type": self.entity_type,
                "description": self.description, "source_ids": self.source_ids,
                "modality": self.modality}


@dataclass
class Relation:
    head: str
    head_type: str
    relation: str
    tail: str
    tail_type: str
    weight: float = 1.0
    description: str = ""
    source_ids: list[str] = field(default_factory=list)
    modality: str = "text"   # V104 多模态: text | table | image

    @property
    def id(self) -> str:
        return hashlib.md5(f"{self.head}::{self.relation}::{self.tail}".encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {"id": self.id, "head": self.head, "head_type": self.head_type,
                "relation": self.relation, "tail": self.tail, "tail_type": self.tail_type,
                "weight": self.weight, "source_ids": self.source_ids,
                "modality": self.modality}


# ── Text Preprocessing (P0-1) ──

# Citation patterns: (Wang et al., 2024), [1], [23,45]
_CITATION_RE = re.compile(
    r'\([A-Z][a-z]+(?:\s+(?:et\s+al\.?|and|&)\s*,?\s*\d{4}[a-z]?)\)'
    r'|\(\w+(?:\s+(?:et\s+al\.?|and)\s*,?\s*)+\d{4}[a-z]?\)'
    r'|\[\d+(?:[,;\s]+\d+)*\]'
)
# URLs
_URL_RE = re.compile(r'https?://\S+')
# Broken word boundaries in PDF text:
# 1. "isDesigned" → 3+ lowercase before uppercase → "is Designed"  
# 2. "MSCOCOhas" → 2+ uppercase before 2+ lowercase → "MSCOCO has"
# 3. "method.PAMIH" → punctuation without space → "method. PAMIH"
_LOWER_UPPER_RE = re.compile(r'([a-z]{3,})([A-Z])')
_UPPER_LOWER_RE = re.compile(r'([A-Z]{2,})([a-z]{2,})')
_PUNCT_SPACE_RE = re.compile(r'([.!?,;:])([A-Za-z\u4e00-\u9fff])')

# Common English words that should never be part of an entity name
_COMMON_WORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "has", "have",
    "are", "was", "were", "been", "not", "but", "can", "will", "its",
    "our", "their", "which", "where", "when", "what", "how", "who",
    "also", "more", "most", "some", "only", "than", "very", "each",
    "into", "over", "such", "both", "same", "may", "any", "new",
    "all", "one", "two", "per", "via", "set", "let", "use", "used",
    "bit", "bits", "at", "by", "of", "on", "in", "to", "is", "it",
    "or", "as", "an", "no", "so", "do", "be",
})


def preprocess_text(text: str) -> str:
    """Fix common PDF text extraction issues before entity extraction."""
    if not text:
        return text
    # Remove URLs first (they contain mixed case)
    text = _URL_RE.sub('', text)
    # Remove academic citations
    text = _CITATION_RE.sub('', text)
    # Fix word boundaries: uppercase sequence followed by lowercase word
    # "MSCOCOhas" → "MSCOCO has", "NDCGof" → "NDCG of"
    text = _UPPER_LOWER_RE.sub(r'\1 \2', text)
    # Fix word boundaries: lowercase word followed by uppercase
    # "isDesigned" → "is Designed"  
    text = _LOWER_UPPER_RE.sub(r'\1 \2', text)
    # Fix missing space after punctuation
    text = _PUNCT_SPACE_RE.sub(r'\1 \2', text)
    # Collapse multiple spaces
    text = re.sub(r'  +', ' ', text)
    return text.strip()


# ── Entity Validation (P0-2) ──

# Blacklist: publisher names, page elements, too-generic words
_BLACKLIST = frozenset({
    # Publishers / boilerplate
    "elsevier", "springer", "ieee", "acm", "arxiv", "wiley",
    "taylor", "francis", "mdpi", "nature", "preprint", "submitted",
    "copyright", "rights", "reserved", "manuscript", "supplementary",
    "page", "vol", "volume", "issue", "journal", "proceedings",
    # Too generic (not entities)
    "method", "approach", "model", "algorithm", "framework",
    "system", "network", "module", "layer", "function", "proposed",
    "result", "experiment", "table", "figure", "section", "appendix",
    "data", "dataset", "set", "input", "output", "feature",
    "image", "text", "label", "class", "task", "problem",
    "training", "testing", "learning", "deep", "neural",
    "performance", "baseline", "comparison", "evaluation",
    "existing", "previous", "recent", "novel", "paper", "work",
    "study", "research", "analysis", "based", "using", "used",
    "show", "shown", "given", "first", "second", "third",
    "however", "therefore", "moreover", "thus", "hence",
    "where", "which", "while", "what", "how", "who",
    "these", "those", "other", "like", "well", "also",
    # Single common words that regex might match
    "the", "this", "that", "with", "from", "have", "for",
    "and", "are", "not", "but", "can", "all", "our",
    "its", "one", "two", "has", "was", "were", "been",
    "each", "into", "than", "over", "such", "more", "most",
    "some", "only", "then", "very", "when", "both", "same",
    "will", "may", "any", "new", "via", "per", "let",
    "map", "loss", "attention",  # These are too ambiguous alone
    # Section headings (all-caps in PDFs)
    "abstract", "introduction", "conclusion", "conclusions",
    "references", "acknowledgment", "acknowledgments", "appendix",
    "statement", "impact", "mathematical", "preliminary",
    "overview", "discussion", "methodology", "background",
    "related", "supplementary", "disclosure", "funding",
    "conflict", "interest", "declaration",
    # Chinese single-char words and function words (not entities)
    "人", "币", "股", "元", "年", "月", "日", "号", "页",
    "的", "了", "在", "是", "有", "和", "与", "或", "及",
    "被", "把", "对", "从", "到", "向", "为", "以", "于",
    "按", "等", "如", "其", "更", "最", "很", "也", "都",
    "已", "将", "会", "能", "可", "要", "应", "该", "中",
    "上", "下", "内", "外", "前", "后", "间", "里", "此",
    # Enterprise boilerplate
    "附注", "综合", "合并", "备考", "审阅", "批准",
    "管理层", "意见", "声明", "公告", "通知", "报告期",
})

# Consecutive lowercase threshold: "isdesignedfor" has too many consecutive lowercase
_CONSECUTIVE_LOWER_RE = re.compile(r'[a-z]{6,}')


def validate_entity_name(name: str) -> bool:
    """Check if a candidate entity name is valid.
    
    Rules:
    - Length: 2-30 characters
    - No periods, commas, semicolons, colons
    - No more than 5 consecutive lowercase letters
    - Not purely numeric
    - Not in blacklist
    - At most 4 space-separated words
    - No common English words concatenated into the name
    """
    if not name or len(name) < 2 or len(name) > 30:
        return False
    if any(c in name for c in '.,:;!?()[]{}'):
        return False
    stripped = name.strip()
    if not stripped or stripped.replace('.', '').replace('-', '').isnumeric():
        return False
    if _CONSECUTIVE_LOWER_RE.search(stripped):
        return False
    if stripped.lower() in _BLACKLIST:
        return False
    if len(stripped.split()) > 4:
        return False
    # Reject names ending with lowercase+digits (garbage like "DDBHat0", "CMCLby0")
    if re.search(r'[a-z]\d+$', stripped) and len(stripped) > 4:
        return False
    # Check for common English words concatenated as SUFFIX
    # "MSCOCOhas80" → "has" after uppercase → reject
    # "NUS-WIDEand" → "and" after uppercase → reject
    # But "VIT-B" → "it" inside acronym → keep (too short, embedded)
    name_lower = stripped.lower()
    name_nohyphen = name_lower.replace("-", "")
    for word in _COMMON_WORDS:
        if len(word) < 3:  # Skip 2-letter words (too many false positives)
            continue
        if word not in name_nohyphen:
            continue
        idx = name_nohyphen.find(word)
        # Only reject if the word appears as a suffix after uppercase chars
        # i.e., the character before the word is uppercase in the original
        if idx > 0 and idx + len(word) >= len(name_nohyphen) - 2:
            # Word is near the end → likely a suffix like "has" in "MSCOCOhas"
            return False
    return True


# ── Entity Extraction Patterns ──

# Academic method abbreviations — MUST be uppercase+digits only
# DCMH, PAMIH, VIT-B, GAN++, TC-12, BERT
# NOT: MSCOCOhas80, NDCGofPAMIH (these have lowercase → garbage)
_METHOD_PATTERN = re.compile(
    r'\b([A-Z]{2,12}(?:[-][A-Z0-9]{1,10})*(?:\+\+)?)\b'  # Pure uppercase: DCMH, VIT-B
    r'|'
    r'\b([A-Z][a-z]{1,4}[A-Z][a-z]{0,4}(?:[A-Z][a-z]{0,4})?)\b'  # CamelCase: DHaPH, ResNet
)

# Known datasets (explicit dictionary for high precision)
_KNOWN_DATASETS = {
    "MIRFLICKR-25K", "MIRFLICKR", "NUS-WIDE", "MS COCO", "MS-COCO", "MSCOCO",
    "COCO", "ImageNet", "CIFAR-10", "CIFAR-100", "MNIST", "Fashion-MNIST",
    "VOC", "Pascal VOC", "SQuAD", "GLUE", "CLUE", "WikiText",
    "CC3M", "LAION", "Flickr30k", "Flickr8k", "Visual Genome",
    "Amazon", "Yelp", "IMDB", "AG News", "SST-2", "CoNLL",
    "OntoNotes", "IAPR TC-12", "IAPR", "IAPRTC-12", "IAPRTC",
    "DuReader", "CMRC", "VOC2007", "VOC2012", "MSCOCO",
    "Natural Questions", "HotpotQA", "TriviaQA", "WebQuestions",
    "WikiQA", "MSMARCO", "MS MARCO", "ShARC", "QuAC", "CoQA",
}
_DATASET_RE = re.compile(
    r'\b(' + '|'.join(re.escape(d) for d in sorted(_KNOWN_DATASETS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)
_DATASET_CHINESE_RE = re.compile(
    r'([^\s,，。；]{2,10}(?:数据集|语料库|基准))',
    re.UNICODE,
)

# Metrics
_METRIC_RE = re.compile(
    r'\b(mAP|MAP|Precision|Recall|F1(?:-score)?|BLEU|ROUGE(?:-[LN12])?|'
    r'NDCG|HR|MRR|AUC|Accuracy|Top-?\d+|P@\d+|R@\d+|IoU|'
    r'FID|SSIM|PSNR|CIDEr|METEOR|C-index|MSE|RMSE|MAE)\b',
    re.IGNORECASE,
)

# Loss functions (must contain "loss" explicitly or be a known loss name)
_LOSS_RE = re.compile(
    r'\b((?:cross[- ]?entropy|triplet|contrastive|focal|hinge|pairwise|'
    r'KL[- ]?divergence|BCE|binary cross[- ]?entropy|softmax)\s+loss)\b'
    r'|'
    r'\b(\w{2,15}\s+loss)\b',
    re.IGNORECASE,
)

# Known tools/frameworks
_TOOLS = {
    "PyTorch", "TensorFlow", "Keras", "FAISS", "HuggingFace", "Transformers",
    "CUDA", "cuDNN", "ONNX", "TensorRT", "OpenCV", "scikit-learn",
    "XGBoost", "LightGBM", "NumPy", "SciPy", "Pandas",
}
_TOOL_RE = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in _TOOLS) + r')\b',
    re.IGNORECASE,
)

# Well-known model architectures (separate from "methods" for type accuracy)
_ARCHITECTURES = {
    "BERT", "GPT", "ViT", "ResNet", "VGG", "LSTM", "GRU",
    "CNN", "RNN", "GAN", "VAE", "Transformer", "U-Net",
    "CLIP", "DALL-E", "Stable Diffusion", "Encoder", "Decoder",
    "AlexNet", "MobileNet", "EfficientNet", "DenseNet", "InceptionNet",
}
_ARCH_RE = re.compile(
    r'\b(' + '|'.join(re.escape(a) for a in _ARCHITECTURES) + r')\b',
    re.IGNORECASE,
)

# Chinese method patterns
_CN_METHOD_RE = re.compile(
    r'(?:提出|使用|基于|采用|利用)(?:了|的)?(?:一种|一个)?'
    r'([^\s,，。；]{2,12}(?:方法|模型|算法|框架|机制|模块|策略))',
    re.UNICODE,
)

# Relation patterns (require entity-like words on both sides)
_RELATION_PATTERNS = [
    (re.compile(r'([A-Z][\w-]{2,20})\s+(?:outperform|surpass|exceed)s?\s+([A-Z][\w-]{2,20})', re.I), "优于"),
    (re.compile(r'([A-Z][\w-]{2,20})\s+(?:is\s+)?based\s+on\s+([A-Z][\w-]{2,20})', re.I), "基于"),
    (re.compile(r'([A-Z][\w-]{2,20})\s+(?:use|employ|adopt)s?\s+([A-Z][\w-]{2,20})', re.I), "使用了"),
    (re.compile(r'([A-Z][\w-]{2,20})\s+(?:propos|introduc)e?s?\s+([A-Z][\w-]{2,20})', re.I), "提出了"),
    (re.compile(r'([A-Z][\w-]{2,20})\s+(?:extend|improv)e?s?\s+([A-Z][\w-]{2,20})', re.I), "扩展了"),
]


class KGExtractor:
    """Local entity/relation extractor with text preprocessing and validation.

    Pipeline per chunk:
      raw text → preprocess → regex extract → validate → deduplicate
    """

    def __init__(self, llm_fn: Callable | None = None):
        self.llm_fn = llm_fn
        self._jieba_ready = False
        self._init_jieba()

    def _init_jieba(self):
        try:
            import jieba
            import jieba.analyse
            jieba.setLogLevel(20)
            self._jieba_ready = True
        except ImportError:
            pass

    def set_llm(self, llm_fn: Callable):
        self.llm_fn = llm_fn

    def set_llm_client(self, client, model="deepseek-chat", **kw):
        self._client = client
        self._model = model
        self.llm_fn = True

    def extract_from_text(self, text: str, source_id: str = "") -> tuple[list[Entity], list[Relation]]:
        """Extract entities and relations from a single text chunk."""
        if not text or len(text.strip()) < 30:
            return [], []

        # Step 1: Preprocess text
        clean = preprocess_text(text)

        # Step 2: Extract candidates
        entities: list[Entity] = []
        seen: set[str] = set()

        def _add(name: str, etype: str):
            name = name.strip()
            key = name.lower()
            if key in seen:
                return
            if not validate_entity_name(name):
                return
            seen.add(key)
            entities.append(Entity(
                name=name, entity_type=etype,
                source_ids=[source_id] if source_id else [],
            ))

        # 2a. Known datasets (extract FIRST — high precision dictionary)
        for m in _DATASET_RE.finditer(clean):
            _add(m.group(1), "数据集")
        for m in _DATASET_CHINESE_RE.finditer(clean):
            _add(m.group(1), "数据集")

        # 2b. Metrics
        for m in _METRIC_RE.finditer(clean):
            _add(m.group(1), "指标")

        # 2c. Tools
        for m in _TOOL_RE.finditer(clean):
            _add(m.group(1), "工具")

        # 2d. Architectures
        for m in _ARCH_RE.finditer(clean):
            _add(m.group(1), "架构")

        # 2e. Loss functions
        for m in _LOSS_RE.finditer(clean):
            name = m.group(1) or m.group(2)
            if name:
                _add(name, "损失函数")

        # 2f. Methods (uppercase abbreviations, at least 3 chars, last to avoid conflicts)
        for m in _METHOD_PATTERN.finditer(clean):
            name = m.group(1) or m.group(2)
            if name and len(name) >= 3:
                _add(name, "方法")

        # 2g. Chinese method names
        for m in _CN_METHOD_RE.finditer(clean):
            _add(m.group(1), "方法")

        # 2h. jieba keywords
        if self._jieba_ready:
            import jieba.analyse
            keywords = jieba.analyse.textrank(clean, topK=5, withWeight=True)
            for word, weight in keywords:
                if weight > 0.4 and len(word) >= 2:
                    _add(word, "概念")

        # Step 3: Extract relations
        relations: list[Relation] = []
        for pattern, rel_type in _RELATION_PATTERNS:
            for m in pattern.finditer(clean):
                head, tail = m.group(1).strip(), m.group(2).strip()
                if validate_entity_name(head) and validate_entity_name(tail):
                    relations.append(Relation(
                        head=head, head_type=self._lookup_type(head, entities),
                        relation=rel_type,
                        tail=tail, tail_type=self._lookup_type(tail, entities),
                        weight=0.8, source_ids=[source_id] if source_id else [],
                    ))

        # Step 4: Co-occurrence relations (conservative: only between entities in same chunk)
        if len(entities) >= 2:
            for i in range(len(entities)):
                for j in range(i + 1, min(i + 3, len(entities))):
                    # Only relate entities of DIFFERENT types (more meaningful)
                    if entities[i].entity_type != entities[j].entity_type:
                        relations.append(Relation(
                            head=entities[i].name, head_type=entities[i].entity_type,
                            relation="相关",
                            tail=entities[j].name, tail_type=entities[j].entity_type,
                            weight=0.3, source_ids=[source_id] if source_id else [],
                        ))

        return entities, relations

    def extract_from_chunks(self, chunks: list[dict],
                            on_progress: Callable[[int, int], None] | None = None,
                            ) -> tuple[list[Entity], list[Relation]]:
        """Extract from multiple chunks with deduplication."""
        all_entities: list[Entity] = []
        all_relations: list[Relation] = []

        for i, chunk in enumerate(chunks):
            text = chunk.get("text", "")
            source_id = chunk.get("chunk_id", chunk.get("doc_id", f"chunk_{i}"))
            if len(text.strip()) < 30:
                continue
            entities, relations = self.extract_from_text(text, source_id)
            all_entities.extend(entities)
            all_relations.extend(relations)
            if on_progress and (i + 1) % 50 == 0:
                on_progress(i + 1, len(chunks))

        merged_e = self._deduplicate_entities(all_entities)
        merged_r = self._deduplicate_relations(all_relations)
        logger.info(f"Extracted {len(merged_e)} entities, {len(merged_r)} relations "
                    f"from {len(chunks)} chunks (local mode)")
        return merged_e, merged_r

    def _lookup_type(self, name: str, entities: list[Entity]) -> str:
        key = name.lower()
        for e in entities:
            if e.name.lower() == key:
                return e.entity_type
        return "方法"

    def _deduplicate_entities(self, entities: list[Entity]) -> list[Entity]:
        merged: dict[str, Entity] = {}
        for e in entities:
            key = e.name.lower().strip()
            if key in merged:
                existing = merged[key]
                existing.source_ids = list(set(existing.source_ids + e.source_ids))
                existing.weight = max(existing.weight, e.weight)
                if len(e.description) > len(existing.description):
                    existing.description = e.description
            else:
                merged[key] = Entity(
                    name=e.name, entity_type=e.entity_type,
                    description=e.description, weight=e.weight,
                    source_ids=list(e.source_ids),
                )
        return list(merged.values())

    def _deduplicate_relations(self, relations: list[Relation]) -> list[Relation]:
        merged: dict[str, Relation] = {}
        for r in relations:
            key = f"{r.head.lower()}::{r.relation.lower()}::{r.tail.lower()}"
            if key in merged:
                existing = merged[key]
                existing.source_ids = list(set(existing.source_ids + r.source_ids))
                existing.weight = min(existing.weight + 0.1, 1.0)
            else:
                merged[key] = Relation(
                    head=r.head, head_type=r.head_type,
                    relation=r.relation, tail=r.tail, tail_type=r.tail_type,
                    weight=r.weight, source_ids=list(r.source_ids),
                )
        return list(merged.values())
