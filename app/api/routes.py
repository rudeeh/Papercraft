from fastapi import APIRouter, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse
import hashlib
import os
import aiofiles
from typing import Optional
from app.core.config import settings
from app.worker.celery_app import celery_app
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
router = APIRouter()

class ChatRequest(BaseModel):
    query: str
    doc_id: Optional[str] = None
    
class ChatResponse(BaseModel):
    answer: str
    citations: list

@router.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")
async def chat(request: Request, body: ChatRequest):
    from app.services.embeddings import get_model
    from app.services.llm import llm_client
    from app.services.vector_store import search_vectors

    # 1. Embed query
    model = get_model()
    query_vector = model.encode(body.query).tolist()
    
    # 2. Search Qdrant
    search_results = search_vectors(query_vector, top_k=settings.RAG_TOP_K, doc_id=body.doc_id)
    
    # 3. Construct Context
    context_text = ""
    citations = []
    
    for hit in search_results:
        payload = hit.payload
        text = payload.get("text", "")
        # Add to context
        context_text += f"---\n{text}\n"
        
        citations.append({
            "doc_id": payload.get("doc_id"),
            "page": payload.get("page"),
            "filename": payload.get("filename"),
            "text_snippet": text[:100] + "..."
        })
    
    # 4. Generate Answer
    system_prompt = "You are a helpful assistant. Use the following context to answer the user's question. If the answer is not in the context, say you don't know."
    prompt = f"Context:\n{context_text}\n\nQuestion: {body.query}"
    
    answer = llm_client.generate_response(prompt, system_prompt=system_prompt)
    
    return {
        "answer": answer,
        "citations": citations
    }

@router.post("/upload")
@limiter.limit("5/minute")
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    force: bool = False
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    # Streaming upload to temp file and calculating hash
    sha256_hash = hashlib.sha256()
    temp_filename = f"temp_{os.urandom(8).hex()}.pdf"
    temp_path = os.path.join(settings.UPLOAD_DIR, temp_filename)
    
    try:
        async with aiofiles.open(temp_path, 'wb') as out_file:
            total_size = 0
            while content := await file.read(1024 * 1024): # 1MB chunks
                total_size += len(content)
                if total_size > settings.MAX_UPLOAD_MB * 1024 * 1024:
                     # Close and delete
                     await out_file.close()
                     os.remove(temp_path)
                     raise HTTPException(status_code=400, detail=f"File exceeds maximum size of {settings.MAX_UPLOAD_MB}MB")
                
                sha256_hash.update(content)
                await out_file.write(content)
    except Exception as e:
        if os.path.exists(temp_path):
             os.remove(temp_path)
        raise e

    # Determine Doc ID
    file_hash = sha256_hash.hexdigest()
    doc_id = file_hash
    final_filename = f"{doc_id}.pdf"
    final_path = os.path.join(settings.UPLOAD_DIR, final_filename)

    if os.path.exists(final_path) and not force:
         os.remove(temp_path) # Delete temp
         return {
             "message": "File already exists. Use force=true to re-process.",
             "doc_id": doc_id,
             "status": "existing"
         }

    # Rename temp to final
    os.rename(temp_path, final_path)

    # Enqueue Task
    task = celery_app.send_task("app.worker.tasks.process_pdf_task", args=[doc_id, final_path])
    
    return {
        "message": "File uploaded and processing started.",
        "doc_id": doc_id,
        "task_id": task.id
    }

@router.get("/status/{task_id}")
async def get_status(task_id: str):
    task_result = celery_app.AsyncResult(task_id)
    
    response = {
        "task_id": task_id,
        "status": task_result.status,
    }
    
    if task_result.status == "PROCESSING":
         response["info"] = task_result.info # Contains the meta dict
    elif task_result.ready():
         response["result"] = task_result.result
    else:
         # PENDING or other states
         pass
         
    return response

@router.get("/health")
async def health_check():
    return {"status": "ok"}
