"""HashMM-RAG Pipeline — document parsing, chunking, and ingestion."""

def __getattr__(name):
    if name == "TextChunker":
        from hashmm.pipeline.chunker import TextChunker
        return TextChunker
    if name == "IngestPipeline":
        from hashmm.pipeline.ingest import IngestPipeline
        return IngestPipeline
    if name == "DocumentParser":
        from hashmm.pipeline.parser import DocumentParser
        return DocumentParser
    raise AttributeError(f"module 'hashmm.pipeline' has no attribute {name}")

__all__ = ["TextChunker", "IngestPipeline", "DocumentParser"]
