# HashMM-RAG 在 AutoDL 上的完整中文教程

> 适用环境:AutoDL 4090 容器,镜像 `cuda12.4-cudnn-devel-ubuntu22.04-py312-torch2.5.1`,通过 JupyterLab 接入。

---

## ⚠️ 重要修复说明

**v0.1.0 → v0.1.1 修复:** 之前的 `pyproject.toml` 把 `[project.urls]` 这段放错了位置,导致 setuptools 把后面的 `dependencies` 当成 `urls.dependencies` 来解析,装包时报奇怪的错误。**v0.1.1 的 zip 已修好**,直接用就行。

如果你看到下面这个报错,就说明用了旧版的 zip,请删除旧的 hashmm-rag 文件夹,重新解压 v0.1.1:

```
configuration error: `project.urls.dependencies` must be string
```

---

## 第 0 步:把项目代码放上 AutoDL

### 方式 A(最简单):JupyterLab 上传

1. 打开 AutoDL 的 JupyterLab,左侧文件浏览器进入 `/root/autodl-tmp/`
2. 把 `hashmm-rag.zip` 拖进去上传
3. 打开一个 Terminal(JupyterLab 左下角 "Other" → "终端")

### 方式 B:命令行

如果你已经下载到本机,用 AutoDL 提供的 ssh / scp 上传到 `/root/autodl-tmp/`。

### 解压

```bash
cd /root/autodl-tmp
unzip -o hashmm-rag.zip
cd hashmm-rag
ls
```

应该能看到这些目录和文件:`hashmm/`、`scripts/`、`tests/`、`docs/`、`pyproject.toml`、`.env.example`、`quickstart.ipynb` 等。

---

## 第 1 步:配置网络加速

你的容器提示「该地区学术加速暂未支持」,所以**必须**手动设置 HuggingFace 镜像,否则下载模型会卡死。

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

**注意**:这个变量只在当前 terminal 里有效。**每开一个新 terminal 都要重设一遍**,或者写到 `~/.bashrc` 一劳永逸:

```bash
echo 'export HF_ENDPOINT=https://hf-mirror.com' >> ~/.bashrc
source ~/.bashrc
```

PyPI 镜像 AutoDL 已经替你配好成阿里云了(从你日志看是 `mirrors.aliyun.com`),不用动。

---

## 第 2 步:安装 Python 依赖

```bash
cd /root/autodl-tmp/hashmm-rag
pip install -e ".[hash,ingest,eval]"
```

**注意常见手误:** 不要打成 `pip install requirements.txt`!那样 pip 会以为你要装一个叫 `requirements.txt` 的包。如果一定要走 requirements 路线,正确写法是 `pip install -r requirements.txt`,前面要加 `-r`。

这一步会下载:
- `transformers`、`accelerate`、`Pillow`、`faiss-cpu`(M2/M3 核心)
- `raganything`、`lightrag-hku`、`mineru[core]`(M1 解析,首次会自动拉 MinerU 的 ~3 GB OCR/版式模型)
- `pandas`、`matplotlib`、`scikit-learn`(评测用)

**估计耗时:5-15 分钟**(MinerU 模型很大,慢一点正常)。

### 装完后做个 sanity check

```bash
python -c "import torch; print('torch:', torch.__version__, 'cuda?', torch.cuda.is_available())"
python -c "import faiss; print('faiss:', faiss.__version__)"
python -c "from raganything import RAGAnything; print('raganything OK')"
python -c "from hashmm.config import HashMMConfig; print('hashmm OK')"
```

如果四行都顺利打印,环境就 OK 了。

---

## 第 3 步:配置 `.env`

```bash
cd /root/autodl-tmp/hashmm-rag
cp .env.example .env
nano .env   # 或者用 vim,或者在 JupyterLab 里点开编辑
```

**至少要改这一项**:

```bash
LLM_API_KEY=sk-你的-deepseek-key
```

其他默认值已经按你的 AutoDL 环境调好了(路径都指向 `/root/autodl-tmp/hashmm/`,embedding 用本地 BGE-M3 省 API 费用,HF 镜像也加上了)。

DeepSeek API key 在 https://platform.deepseek.com/ 申请,新用户有免费额度。

---

## 第 4 步:预下载模型(可选但推荐)

模型在第一次跑脚本时会自动下载,但提前下好可以避免脚本中途卡半天看不到进度。下面这几条命令把 HuggingFace 上的模型一次性拉下来:

```bash
# 确保 HF 镜像生效
export HF_ENDPOINT=https://hf-mirror.com

# BGE-M3(文本编码器,~2.3 GB)
python -c "from transformers import AutoModel, AutoTokenizer; \
           AutoTokenizer.from_pretrained('BAAI/bge-m3'); \
           AutoModel.from_pretrained('BAAI/bge-m3'); \
           print('BGE-M3 OK')"

# SigLIP-2(图像编码器,~400 MB)
python -c "from transformers import AutoModel, AutoProcessor; \
           AutoProcessor.from_pretrained('google/siglip2-base-patch16-256'); \
           AutoModel.from_pretrained('google/siglip2-base-patch16-256'); \
           print('SigLIP-2 OK')"
```

模型缓存在 `/root/autodl-tmp/hashmm/.hf_cache/`(持久盘),下次重启容器还在。

MinerU 的解析模型会在第 5 步首次运行时自动下载(它内部用的不是 HF,所以不受 HF 镜像影响,走 modelscope 通常很快)。

---

## 第 5 步:跑完整流程

### 5.1 下载几篇 arXiv 论文当样本数据

```bash
cd /root/autodl-tmp/hashmm-rag
python scripts/00_download_arxiv.py --output /root/autodl-tmp/hashmm/data/pdfs
```

默认会下 6 篇相关论文(HASH-RAG、RAG-Anything、ColPali、SigLIP-2、BGE-M3、LightRAG)。如果你想换成自己的 PDF,把它们直接拷到 `/root/autodl-tmp/hashmm/data/pdfs/` 就行。

### 5.2 解析 PDF

```bash
python scripts/01_parse_documents.py \
    --input  /root/autodl-tmp/hashmm/data/pdfs \
    --output /root/autodl-tmp/hashmm/data/parsed
```

**首次运行**会触发 MinerU 模型下载(~3 GB),时间长。如果 30 分钟还没动静,Ctrl+C 后手动跑:

```bash
mineru-models-download --source modelscope
```

然后再回来跑 01。

### 5.3 提取 chunks 和跨模态训练对

```bash
python scripts/02_extract_pairs.py \
    --parsed /root/autodl-tmp/hashmm/data/parsed \
    --chunks /root/autodl-tmp/hashmm/data/chunks.jsonl \
    --pairs  /root/autodl-tmp/hashmm/data/pairs.jsonl
```

很快,几秒钟。完成后看下统计:

```bash
wc -l /root/autodl-tmp/hashmm/data/chunks.jsonl
wc -l /root/autodl-tmp/hashmm/data/pairs.jsonl
```

**重要:6 篇 arXiv 论文大约能产出 30-60 对训练样本——只够跑通流程,训练不出好结果。如果想认真做实验,至少塞 50+ 篇论文进 `pdfs/`。**

### 5.4 训练跨模态哈希网络

```bash
python scripts/03_train_hash_net.py \
    --pairs /root/autodl-tmp/hashmm/data/pairs.jsonl
```

20 个 epoch,6 篇论文规模下大约 30-60 分钟。每个 epoch 末尾会打印验证集的 mAP@10:

```
epoch 5/20  τ=2.89  loss=0.4321 (pair=0.4012 q=0.0285 bal=0.0024)
            mAP@10 t2i=0.523 i2t=0.508  [42.1s]
```

理想情况下到第 10 个 epoch,`t2i` 和 `i2t` 都应该 > 0.5。如果一直 < 0.3,通常是数据量太小,加更多 PDF。

显存:大约 14-18 GB,4090 的 23 GB 够用。如果 OOM,改 `.env` 里的 `HASH_BATCH_SIZE=32`(默认 64)。

### 5.5 编码全部 chunks 并建索引

```bash
python scripts/04_build_index.py \
    --chunks /root/autodl-tmp/hashmm/data/chunks.jsonl
```

把所有 chunks 用训好的哈希网络编码成 128 位二进制码,存进 Faiss `IndexBinaryFlat`。结束后会打印索引大小:

```
✓ 8421 items × 128 bits = 134,736 bytes (0.13 MiB)
```

对比一下:同样数据用 dense float32 1024 维要 32 MB,**节省 240 倍存储**。

### 5.6 查询测试

```bash
# 文本查 → 自动跨模态(可以返回 image/table/equation)
python scripts/05_query.py --query "attention mechanism in transformer"

# 限定只看 image
python scripts/05_query.py --query "system architecture diagram" --modality image

# 图查 → 用图片当 query,找相关文本/图片
python scripts/05_query.py --query "" --image /root/autodl-tmp/hashmm/output/某文件夹/images/0001.jpg
```

输出是 Rich 的彩色表格,有 hamming 距离、模态、文档/页码、文本片段。

---

## 第 6 步:用 Jupyter Notebook 走一遍(可选,更直观)

JupyterLab 左侧打开 `quickstart.ipynb`,从上到下点 Shift+Enter 一格一格运行。每步之间有结果检查(看 chunks 分布、查看 cross-modal pair 样例、训练日志等),适合第一次跑搞清楚每步在做什么。

---

## 常见报错与处理

### ❌ `configuration error: project.urls.dependencies must be string`

**原因:** 用了 v0.1.0 旧 zip(我之前的 bug)。  
**修复:** 用 v0.1.1 重新解压。或者手动改 `pyproject.toml`,把 `[project.urls]` 段移到 `[project]` 段所有内容的后面(在 `[project.optional-dependencies]` 后面、`[tool.setuptools.packages.find]` 前面)。

### ❌ `OSError: We couldn't connect to huggingface.co`

**原因:** HF 镜像没生效。  
**修复:**
```bash
export HF_ENDPOINT=https://hf-mirror.com
# 检查是否真的生效:
env | grep HF_ENDPOINT
```

### ❌ `CUDA out of memory`

**修复:** 编辑 `.env`,把 `HASH_BATCH_SIZE=64` 改成 `HASH_BATCH_SIZE=32` 或 `16`,再跑 03。

### ❌ `pip install requirements.txt` 报 "No matching distribution"

**原因:** 漏写了 `-r`。  
**修复:** 用 `pip install -r requirements.txt`,或者按我推荐的 `pip install -e ".[hash,ingest,eval]"`。

### ❌ MinerU 模型下载很慢或挂了

**修复:**
```bash
mineru-models-download --source modelscope
```
modelscope 在国内基本秒下。或者改用 docling 解析器:`.env` 里设 `PARSER=docling`(没 OCR 但通用 PDF 解析能用)。

### ❌ 训练 loss 出现 NaN

**修复:** `.env` 里把 `HASH_TANH_TEMPERATURE_END` 从 10.0 改小到 5.0,温度退火太激进会让 tanh 饱和导致梯度爆炸。

### ❌ 训练时 val mAP 一直接近 0

**原因:** 数据太少。6 篇论文产生的 ~50 对训练样本,网络学不会跨模态对齐。  
**修复:** 把更多 PDF 拷进 `data/pdfs/` 重跑 01-04。学术论文用 arxiv 批量下载脚本扩展 `00_download_arxiv.py` 里 `DEFAULT_IDS` 即可。

---

## 跑完之后给我回传这些信息

(参见 `docs/NEXT_STEPS.md`)

1. **训练日志最后 5 行**(看 mAP@10 数字)
2. ```bash
   wc -l /root/autodl-tmp/hashmm/data/chunks.jsonl
   wc -l /root/autodl-tmp/hashmm/data/pairs.jsonl
   ```
3. **一个 `05_query.py` 的查询结果**(截图或复制表格文字)
4. **任何报错的完整 traceback**

我拿到这四样就知道是要先调参,还是可以开始建 Agent 层(M4)。
