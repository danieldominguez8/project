from flask import Flask, send_file, jsonify, request
import os
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, ArrayObject, NameObject, BooleanObject
import webbrowser
import threading
import time

app = Flask(__name__, static_folder="static")
FILES_FOLDER = "files"

def extract_fields(pdf_path):
    """Extract form field names from a PDF."""
    try:
        with open(pdf_path, "rb") as file:
            reader = PdfReader(file)
            fields = reader.get_fields()
            field_names = list(fields.keys()) if fields else []
            # Removed logging: print(f"Fields in {pdf_path}: {field_names}")
            return field_names
    except Exception as e:
        # Removed logging: print(f"Error extracting fields from {pdf_path}: {e}")
        return []

# Load PDFs from the files folder at startup
pdf_data = {}
for filename in os.listdir(FILES_FOLDER):
    if filename.endswith(".pdf"):
        file_path = os.path.join(FILES_FOLDER, filename)
        fields = extract_fields(file_path)
        pdf_data[filename] = {"path": file_path, "fields": fields}

def fill_pdf_form(input_path, output_stream, field_values):
    """Fill PDF form fields with provided values, skipping signature fields."""
    try:
        reader = PdfReader(input_path)
        writer = PdfWriter()
        
        # Copy all pages from reader to writer
        for page in reader.pages:
            writer.add_page(page)
        
        # Handle AcroForm: if it exists, use it; otherwise create a minimal one.
        if '/AcroForm' in reader.trailer:
            acroform = reader.trailer['/AcroForm']
            try:
                acroform = acroform.get_object()
            except AttributeError:
                pass
            writer._root_object[NameObject('/AcroForm')] = acroform
        else:
            fields = reader.get_fields()
            if fields:
                acroform = DictionaryObject()
                acroform.update({
                    NameObject('/Fields'): ArrayObject(),
                    NameObject('/NeedAppearances'): BooleanObject(True)
                })
                writer._root_object[NameObject('/AcroForm')] = acroform
            else:
                pass
        
        # Remove signature fields and empty values
        cleaned_values = {
            k: str(v)
            for k, v in field_values.items()
            if k and v and "signature" not in k.lower()
        }
        
        if cleaned_values:
            for page in writer.pages:
                writer.update_page_form_field_values(page, cleaned_values)
        
        writer.write(output_stream)
    except Exception as e:
        raise Exception(f"Form fill error: {str(e)}")

def fill_pdf(input_path, output_stream, field_values):
    reader = PdfReader(input_path)
    fields = reader.get_fields()
    has_fields = bool(fields)
    
    if has_fields:
        fill_pdf_form(input_path, output_stream, field_values)
    else:
        raise Exception("This PDF does not have fillable form fields.")

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
        return jsonify({"error": str(e)}), 500

def open_browser():
    time.sleep(1)
    webbrowser.open("http://127.0.0.1:5000/")

if __name__ == "__main__":
    threading.Thread(target=open_browser).start()
    app.run(debug=True)
