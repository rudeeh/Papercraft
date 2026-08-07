from typing import List, Tuple
from app.core.config import settings

def chunk_text(pages_text: List[Tuple[int, str]], doc_id: str) -> List[dict]:
    """
    Chunks text with overlap.
    pages_text: list of (page_num, text)
    Returns list of dicts: { "text": chunk_text, "metadata": { "doc_id": ..., "page": ... } }
    """
    chunks = []
    chunk_size = settings.CHUNK_TOKENS
    overlap = settings.CHUNK_OVERLAP_TOKENS
    
    # We'll use a simple word-based chunker for now, or character based.
    # Token-based requires a tokenizer (tiktoken or transformers).
    # "Chunk text semantically or by token limits"
    # To keep it simple and dependency light, we can approximate tokens by words (1 word ~ 0.75 tokens? or just space split).
    # Let's use space split for simplicity.
    
    for page_num, text in pages_text:
        words = text.split()
        if not words:
            continue
            
        i = 0
        while i < len(words):
            # End index
            end = min(i + chunk_size, len(words))
            chunk_words = words[i:end]
            chunk_str = " ".join(chunk_words)
            
            chunks.append({
                "text": chunk_str,
                "metadata": {
                    "doc_id": doc_id,
                    "page": page_num,
                    "filename": f"{doc_id}.pdf"
                }
            })
            
            # Move forward by (chunk_size - overlap)
            i += (chunk_size - overlap)
            
            # Ensure we don't get stuck if overlap >= chunk_size (bad config)
            if chunk_size <= overlap:
                i += 1
                
    return chunks
