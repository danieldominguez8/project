from flask import Flask, send_file, jsonify, request
import os
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, ArrayObject, NameObject, BooleanObject
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

app = Flask(__name__, static_folder="static")
FILES_FOLDER = "files"

def extract_fields(pdf_path):
    """Extract form field names from a PDF."""
    try:
        with open(pdf_path, "rb") as file:
            reader = PdfReader(file)
            fields = reader.get_fields()
            field_names = list(fields.keys()) if fields else []
            print(f"Fields in {pdf_path}: {field_names}")
            if not fields:
                print(f"WARNING: No form fields found in {pdf_path}")
            return field_names
    except Exception as e:
        print(f"Error extracting fields from {pdf_path}: {e}")
        return []

# Load PDFs from files/ at startup
pdf_data = {}
for filename in os.listdir(FILES_FOLDER):
    if filename.endswith(".pdf"):
        file_path = os.path.join(FILES_FOLDER, filename)
        fields = extract_fields(file_path)
        pdf_data[filename] = {"path": file_path, "fields": fields}

def fill_pdf_form(input_path, output_stream, field_values):
    """Fill PDF form fields with provided values."""
    try:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        
        # Copy all pages from reader to writer
        for page in reader.pages:
            writer.add_page(page)
        
        # Debug trailer contents
        print(f"Reader trailer keys: {list(reader.trailer.keys())}")
        
        # Check for /AcroForm in trailer
        if '/AcroForm' in reader.trailer:
            print(f"AcroForm found: {reader.trailer['/AcroForm']}")
            writer._root_object['/AcroForm'] = reader.trailer['/AcroForm'].get_object()
        else:
            # If fields detected but no /AcroForm in trailer, create a minimal one
            fields = reader.get_fields()
            if fields:
                print("No /AcroForm in trailer, but fields detected - forcing creation")
                acroform = DictionaryObject()
                acroform.update({
                    NameObject('/Fields'): ArrayObject(),
                    NameObject('/NeedAppearances'): BooleanObject(True)
                })
                writer._root_object['/AcroForm'] = writer._add_object(acroform)
            else:
                print("No fields or /AcroForm - this shouldn’t happen here")
        
        # Prepare field values
        cleaned_values = {k: str(v) for k, v in field_values.items() if k and v}
        print(f"Filling form fields in {input_path} with: {cleaned_values}")
        
        if not cleaned_values:
            print("No values to fill, skipping update")
        else:
            # Update form field values for each page
            for page in writer.pages:
                writer.update_page_form_field_values(page, cleaned_values)
        
        writer.write(output_stream)
    except Exception as e:
        raise Exception(f"Form fill error: {str(e)}")

def overlay_text(input_path, output_stream, field_values):
    """Overlay text for non-fillable PDFs at specific coordinates."""
    try:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        packet = BytesIO()
        c = canvas.Canvas(packet, pagesize=letter)
        
        if "AccountHolderName" in field_values:
            c.drawString(150, 700, field_values["AccountHolderName"])
        if "Customer Signature" in field_values:
            c.drawString(150, 650, field_values["Customer Signature"])
        if "AccountHolderDateSigned" in field_values:
            c.drawString(150, 600, field_values["AccountHolderDateSigned"])
        
        c.showPage()
        c.save()
        packet.seek(0)
        overlay_pdf = PdfReader(packet)
        
        for page_num in range(len(reader.pages)):
            page = reader.pages[page_num]
            if page_num == 0:
                page.merge_page(overlay_pdf.pages[0])
            writer.add_page(page)
        
        print(f"Overlaying text in {input_path} with: {field_values}")
        writer.write(output_stream)
    except Exception as e:
        raise Exception(f"Overlay error: {str(e)}")

def fill_pdf(input_path, output_stream, field_values):
    """Decide whether to fill form fields or overlay text."""
    reader = PdfReader(input_path)
    fields = reader.get_fields()
    has_fields = bool(fields)
    print(f"PDF {input_path} has fields: {has_fields}")
    if has_fields:
        print(f"Detected fields: {list(fields.keys())}")
        fill_pdf_form(input_path, output_stream, field_values)
    else:
        overlay_text(input_path, output_stream, field_values)

@app.route("/")
def serve_index():
    return app.send_static_file("index.html")

@app.route("/pdfs", methods=["GET"])
def get_pdfs():
    return jsonify({"pdfs": list(pdf_data.keys())})

@app.route("/fields/<pdf_name>", methods=["GET"])
def get_fields(pdf_name):
    if pdf_name in pdf_data:
        return jsonify({"fields": pdf_data[pdf_name]["fields"]})
    return jsonify({"error": "PDF not found"}), 404

@app.route("/fill", methods=["POST"])
def fill_and_export():
    data = request.json
    pdf_name = data["pdf"]
    field_values = data["fields"]
    if pdf_name not in pdf_data:
        return jsonify({"error": "PDF not found"}), 404
    
    try:
        output_stream = BytesIO()
        fill_pdf(pdf_data[pdf_name]["path"], output_stream, field_values)
        output_stream.seek(0)
        return send_file(
            output_stream,
            as_attachment=True,
            download_name=f"filled_{pdf_name}",
            mimetype="application/pdf"
        )
    except Exception as e:
        print(f"Export error for {pdf_name}: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)