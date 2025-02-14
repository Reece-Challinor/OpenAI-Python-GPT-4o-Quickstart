
"""
FastAPI Application for Actuarial Memorandum Analysis
This application provides an API for analyzing actuarial memorandums using GPT-4.
It includes file upload capabilities, text extraction from PDFs, and storage in PostgreSQL.

Author: [Your Name]
Date: [Current Date]
Version: 1.0.0
"""

import logging
import logging.handlers
import os

# ============================================================================
# Logging Configuration
# ============================================================================

# Configure logging directory
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

# Initialize logger for the application
logger = logging.getLogger('asop_api')
logger.setLevel(logging.INFO)

# Configure rotating file handler for all logs
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(log_dir, 'asop_api.log'),
    maxBytes=1024*1024,  # 1MB
    backupCount=5        # Keep 5 backup files
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))
logger.addHandler(file_handler)

# Configure console handler for error logs only
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)
console_handler.setFormatter(logging.Formatter(
    '%(levelname)s: %(message)s'
))
logger.addHandler(console_handler)

# Import required libraries
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import openai
import os
from datetime import datetime
import fitz  # PyMuPDF
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from typing import List

# ============================================================================
# PDF Processing Functions
# ============================================================================

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract text content from a PDF file.
    
    Args:
        pdf_path (str): Path to the PDF file
        
    Returns:
        str: Extracted text from the PDF
        
    Raises:
        HTTPException: If text extraction fails
    """
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error extracting text from PDF: {str(e)}")

# Create uploads directory for storing PDF files
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ============================================================================
# FastAPI Application Configuration
# ============================================================================

# Initialize FastAPI app with metadata
app = FastAPI(
    title="Actuarial Memorandum Analysis API",
    description="API for analyzing actuarial memorandums using GPT-4",
    version="1.0.0"
)

# ============================================================================
# Database Configuration and Setup
# ============================================================================

# Get database URL from environment variables
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not found in environment variables")

# Fix the connection string formatting
if "?sslmode=" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

# Create connection pool with min 1 and max 5 connections
db_pool = pool.SimpleConnectionPool(1, 5, dsn=DATABASE_URL)

def get_db_connection():
    """Get a connection from the database pool."""
    return db_pool.getconn()

def release_db_connection(conn):
    """Release a connection back to the pool."""
    db_pool.putconn(conn)

def init_db():
    """
    Initialize database by creating required tables if they don't exist.
    Creates 'uploads' table and necessary indexes.
    """
    conn = None
    try:
        logger.info("Initializing database")
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS uploads (
                    id SERIAL PRIMARY KEY,
                    filename TEXT NOT NULL,
                    extracted_text TEXT NOT NULL,
                    asop_analysis JSONB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_created_at ON uploads (created_at);
            """)
            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            logger.error(f"Database initialization error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database initialization error: {str(e)}")
        finally:
            cur.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to database: {str(e)}")
    finally:
        if conn:
            release_db_connection(conn)

# Initialize database on startup
init_db()

# ============================================================================
# CORS Configuration
# ============================================================================

# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# OpenAI Configuration
# ============================================================================

# Configure OpenAI API key
openai.api_key = os.environ.get('OPENAI_API_KEY')
if not openai.api_key:
    raise RuntimeError("OPENAI_API_KEY not found in environment variables")

# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint to check if API is running."""
    return {"message": "ASOP Compliance API is running!"}

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {"status": "healthy"}

@app.post("/analyze")
async def analyze_memo(text: str):
    """
    Analyze provided text using GPT-4 for ASOP compliance.
    
    Args:
        text (str): Text content to analyze
        
    Returns:
        dict: Analysis results from GPT-4
    """
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
    """
    Upload and process a PDF file for ASOP compliance analysis.
    
    Args:
        file (UploadFile): The PDF file to be analyzed
        
    Returns:
        dict: Upload results including analysis
    """
    conn = None
    try:
        logger.info(f"Processing upload request for file: {file.filename}")
        if not file.filename.lower().endswith('.pdf'):
            logger.warning(f"Invalid file type attempted: {file.filename}")
            raise HTTPException(status_code=400, detail="Only PDF files are allowed")
        
        # Create unique filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{file.filename}"
        file_path = os.path.join(UPLOAD_DIR, filename)
        
        # Save uploaded file
        contents = await file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
        
        # Extract text from PDF
        text = extract_text_from_pdf(file_path)
        
        # Analyze text using GPT-4
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
        
        # Store results in database
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            import json
            analysis_json = json.dumps({"analysis": analysis})
            cur.execute(
                "INSERT INTO uploads (filename, extracted_text, asop_analysis) VALUES (%s, %s, %s::jsonb) RETURNING id",
                (filename, text, analysis_json)
            )
            upload_id = cur.fetchone()[0]
            conn.commit()
        except psycopg2.Error as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Database operation failed: {str(e)}")
        finally:
            cur.close()
            if conn:
                release_db_connection(conn)

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

@app.get("/documents/")
async def get_documents():
    """
    Retrieve list of all processed documents.
    
    Returns:
        dict: List of document metadata
    """
    logger.info("Retrieving document list")
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
    """
    Retrieve a specific document by ID.
    
    Args:
        doc_id (int): Document ID to retrieve
        
    Returns:
        dict: Document details including analysis
    """
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

# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
