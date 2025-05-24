from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import fitz  # PyMuPDF
import qrcode
import tempfile
import os
import io
import uuid
from pydantic import BaseModel
from typing import Optional, List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=bool(os.environ.get("ALLOW_CREDENTIALS", "True") == "True"),
    allow_methods=os.environ.get("ALLOWED_METHODS", "GET,POST,PUT,DELETE").split(","),
    allow_headers=os.environ.get("ALLOWED_HEADERS", "*").split(","),
)

class SignPosition(BaseModel):
    x: float
    y: float
    page: int
    width: Optional[float] = 100
    height: Optional[float] = 100

@app.post("/detect-sign-positions")
async def detect_sign_positions(
    file: UploadFile = File(...),
    marker: str = Form("[[SIGN_HERE]]")
):
    """Detect positions of markers in PDF without modifying it"""
    try:
        # Read uploaded PDF
        pdf_data = await file.read()
        
        # Process with PyMuPDF
        positions = find_marker_positions(pdf_data, marker)
        
        return {"positions": positions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {str(e)}")

@app.post("/add-qr-codes")
async def add_qr_codes(
    file: UploadFile = File(...),
    positions_json: str = Form(...),  # JSON string of positions
    qr_data: str = Form(...),  # Data for QR code
    certificate_id: str = Form(None)
):
    """Add QR codes to specific positions in PDF"""
    import json
    
    try:
        # Parse the positions
        positions = json.loads(positions_json)
        
        # Read uploaded PDF
        pdf_data = await file.read()
        
        # Create a temporary file for the modified PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_path = temp_file.name
            
        # Add QR codes to the PDF
        modified_pdf = add_qr_to_pdf(pdf_data, positions, qr_data, certificate_id)
        
        # Write to temporary file
        with open(temp_path, "wb") as f:
            f.write(modified_pdf)
        
        # Return the modified PDF
        return FileResponse(
            temp_path,
            media_type="application/pdf",
            filename=f"signed_{file.filename}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding QR codes: {str(e)}")

def find_marker_positions(pdf_data: bytes, marker: str = "[[SIGN_HERE]]") -> List[SignPosition]:
    """Find positions of markers in the PDF"""
    positions = []
    
    # Open PDF from memory
    doc = fitz.open(stream=pdf_data, filetype="pdf")
    
    # Search for markers in each page
    for page_num, page in enumerate(doc):
        # Search for the marker text
        text_instances = page.search_for(marker)
        
        # Add each marker position
        for rect in text_instances:
            # Convert to SignPosition (rect is a fitz.Rect object with x0, y0, x1, y1)
            positions.append(SignPosition(
                x=rect.x0,  # Left position
                y=rect.y0,  # Top position
                page=page_num,
                width=rect.width,
                height=rect.height
            ))
    
    return positions

def add_qr_to_pdf(pdf_data: bytes, positions: List[dict], qr_data: str, certificate_id: str = None) -> bytes:
    """Add QR codes to the PDF at specified positions"""
    # Open PDF from memory
    doc = fitz.open(stream=pdf_data, filetype="pdf")
    
    # Generate QR code with improved settings
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=1,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    
    # Create high-quality image
    img_bytes = io.BytesIO()
    qr_img.save(img_bytes, format='PNG', optimize=True, quality=95)
    img_bytes.seek(0)
    
    # Add QR code to each position
    for pos in positions:
        # Check if pos is a SignPosition object or a dict
        if hasattr(pos, 'page'):
            page_num = pos.page
            page = doc[page_num]
            x = pos.x
            y = pos.y
            width = getattr(pos, 'width', 120)
            height = getattr(pos, 'height', 120)
        else:
            page_num = pos.get("page", 0)
            page = doc[page_num]
            x = pos.get("x", 50)
            y = pos.get("y", 50)
            width = pos.get("width", 120)
            height = pos.get("height", 120)
        
        # First, remove the original [[SIGN_HERE]] marker text
        # Create a white rectangle over the marker text to remove it
        marker_rect = fitz.Rect(x, y, x + width, y + height)
        page.draw_rect(marker_rect, color=(1, 1, 1), fill=(1, 1, 1))
        
        # Get page dimensions to ensure QR code stays within bounds
        page_width = page.rect.width
        page_height = page.rect.height
        
        # Calculate a better QR size
        qr_size = 60  # Smaller QR code
        
        # Adjust position to place QR code more appropriately
        # Position it to the right of where the marker was
        adjusted_x = min(x + 10, page_width - qr_size - 10)
        adjusted_y = y - 30  # Move it slightly up from the marker position
        
        # Make sure QR code is within page bounds
        adjusted_x = max(10, min(adjusted_x, page_width - qr_size - 10))
        adjusted_y = max(10, min(adjusted_y, page_height - qr_size - 40))  # Leave room for text below
        
        # Draw white background rectangle with subtle shadow for better visibility
        # Shadow effect
        page.draw_rect(
            fitz.Rect(adjusted_x-2, adjusted_y-2, adjusted_x + qr_size + 4, adjusted_y + qr_size + 4),
            color=(0.8, 0.8, 0.8),
            fill=(0.8, 0.8, 0.8)
        )
        
        # White background
        page.draw_rect(
            fitz.Rect(adjusted_x, adjusted_y, adjusted_x + qr_size, adjusted_y + qr_size),
            color=(1, 1, 1),
            fill=(1, 1, 1)
        )
        
        # Add QR code image
        page.insert_image(
            fitz.Rect(adjusted_x, adjusted_y, adjusted_x + qr_size, adjusted_y + qr_size),
            stream=img_bytes.getvalue()
        )
        
        # Add certificate text if provided (with improved styling)
        if certificate_id:
            # Text below QR - limit to just two lines with smaller font
            text_y = adjusted_y + qr_size + 2
            
            # Add "Verified Document" text
            page.insert_text(
                point=(adjusted_x, text_y),
                text="Verified Document",
                fontsize=7,
                fontname="Helvetica-Bold",
                color=(0, 0, 0.7)
            )
            
            # Add certificate ID (compacted format)
            page.insert_text(
                point=(adjusted_x, text_y + 10),
                text=f"{certificate_id}",
                fontsize=6
            )
    
    # Save the modified PDF
    output_bytes = io.BytesIO()
    doc.save(output_bytes, garbage=3, deflate=True)
    doc.close()
    
    return output_bytes.getvalue()

@app.post("/detect-and-add-qr")
async def detect_and_add_qr(
    file: UploadFile = File(...),
    marker: str = Form("[[SIGN_HERE]]"),
    qr_data: str = Form(...),
    certificate_id: Optional[str] = Form(None)
):
    """Detect markers and add QR codes in one step"""
    try:
        # Read uploaded PDF
        pdf_data = await file.read()
        
        # Find marker positions
        positions = find_marker_positions(pdf_data, marker)
        
        if not positions:
            return JSONResponse(
                status_code=404,
                content={"error": f"No marker '{marker}' found in the document"}
            )
        
        # Add QR codes
        modified_pdf = add_qr_to_pdf(pdf_data, positions, qr_data, certificate_id)
        
        # Create a temporary file for the modified PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
            temp_file.write(modified_pdf)
            temp_path = temp_file.name
        
        # Return the modified PDF
        return FileResponse(
            temp_path,
            media_type="application/pdf",
            filename=f"signed_{file.filename}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {str(e)}")

# Alternative marker detection using regular expressions for more complex patterns
@app.post("/detect-with-regex")
async def detect_with_regex(
    file: UploadFile = File(...),
    pattern: str = Form(r"\[\[SIGN[_\s]?HERE\]\]")  # Default regex pattern
):
    """Detect positions using regex patterns"""
    try:
        # Implementation would be similar but use regex-based search
        # This would require using libraries like pdfminer or pdfplumber
        # for more advanced text extraction and pattern matching
        
        # Placeholder response
        return {"message": "Regex-based detection not implemented yet"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error with regex detection: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    
    # Get configuration from environment variables
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8000))
    reload = os.environ.get("RELOAD", "False").lower() == "true"
    
    uvicorn.run(app, host=host, port=port, reload=reload)