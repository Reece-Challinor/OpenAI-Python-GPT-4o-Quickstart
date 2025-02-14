
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import openai
import os
from datetime import datetime
import fitz  # PyMuPDF

def extract_text_from_pdf(pdf_path: str) -> str:
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error extracting text from PDF: {str(e)}")

# Create uploads directory
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List

# Initialize FastAPI app
app = FastAPI(
    title="Actuarial Memorandum Analysis API",
    description="API for analyzing actuarial memorandums using GPT-4",
    version="1.0.0"
)

# Database connection
from psycopg2 import pool

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found in environment variables")

DATABASE_URL += "?sslmode=require"
db_pool = pool.SimpleConnectionPool(1, 5, DATABASE_URL)  # Min 1, Max 5 connections

def get_db_connection():
    return db_pool.getconn()

def release_db_connection(conn):
    db_pool.putconn(conn)

# Initialize database table
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS uploads (
                id SERIAL PRIMARY KEY,
                filename TEXT NOT NULL,
                extracted_text TEXT NOT NULL,
                asop_analysis TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()

init_db()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure OpenAI
openai.api_key = os.environ.get('OPENAI_API_KEY')
if not openai.api_key:
    raise RuntimeError("OPENAI_API_KEY not found in environment variables")

@app.get("/")
async def root():
    return {"message": "ASOP Compliance API is running!"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.post("/analyze")
async def analyze_memo(text: str):
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "You are an actuarial expert analyzing memorandums."
                },
                {
                    "role": "user",
                    "content": text
                }
            ]
        )
        return {"analysis": response.choices[0].message.content}
    except openai.OpenAIError as oe:
        raise HTTPException(status_code=400, detail=f"OpenAI API Error: {str(oe)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

@app.post("/upload/")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    # Create unique filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, filename)
    
    try:
        # Save the file
        contents = await file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
        
        # Extract text from the PDF
        text = extract_text_from_pdf(file_path)
        
        # Analyze text for ASOP compliance
        try:
            response = openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an actuarial expert. Analyze this actuarial memorandum for ASOP compliance. Provide specific insights about compliance with actuarial standards of practice."
                    },
                    {
                        "role": "user",
                        "content": text
                    }
                ]
            )
            analysis = response.choices[0].message.content
        except openai.OpenAIError as oe:
            analysis = f"OpenAI API Error: {str(oe)}"
            raise HTTPException(status_code=400, detail=analysis)
        except Exception as e:


@app.get("/documents/")
async def get_documents():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT id, filename, created_at 
            FROM uploads 
            ORDER BY created_at DESC
        """)
        documents = cur.fetchall()
        return {"documents": documents}
    finally:
        cur.close()
        release_db_connection(conn)

@app.get("/documents/{doc_id}")
async def get_document(doc_id: int):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            SELECT * FROM uploads WHERE id = %s
        """, (doc_id,))
        document = cur.fetchone()
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        return document
    finally:
        cur.close()
        release_db_connection(conn)


            analysis = f"Error during analysis: {str(e)}"
        
        # Store in database
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO uploads (filename, extracted_text, asop_analysis) VALUES (%s, %s, %s) RETURNING id",
                (filename, text, analysis)
            )
            upload_id = cur.fetchone()[0]
            conn.commit()
        finally:
            cur.close()
            conn.close()

        return {
            "message": "File uploaded and analyzed successfully",
            "id": upload_id,
            "filename": filename,
            "path": file_path,
            "extracted_text": text,
            "asop_analysis": analysis
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
