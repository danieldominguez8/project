from flask import Flask, send_file, jsonify, request
import os
from io import BytesIO
import webbrowser
import threading
import time
import logging
import re # For display name generation

# pypdf import
from pypdf import PdfReader, PdfWriter
# Import necessary types from pypdf.generic
from pypdf.generic import DictionaryObject, ArrayObject, NameObject, BooleanObject, TextStringObject, NumberObject, IndirectObject

app = Flask(__name__, static_folder="static")
FILES_FOLDER = "files" # PDFs to be filled are here

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants for PDF field flags (values are 2^(bit_position-1))
PDF_FIELD_FLAG_READ_ONLY = 1 << (1 - 1)
PDF_FIELD_FLAG_RADIO = 1 << (16 - 1)
PDF_FIELD_FLAG_PUSHBUTTON = 1 << (17 - 1)
# Add other flags if needed (e.g., REQUIRED, NO_EXPORT, etc.)


# --- Enhanced Helper Function for Display Names ---
def format_field_name_for_display(name):
    """Converts internal PDF field names to more readable display names."""
    if not name:
        return ""
    # Specific replacements (case-insensitive pattern matching)
    specific_replacements = {
        "Dateofbirth": "Date of Birth", "Date Of Birth": "Date of Birth",
        "Orunemployed": "Or Unemployed", "Retiredorunemployed": "Retired or Unemployed",
        "Retiredor": "Retired or", "Accountholder": "Account Holder",
        "Holderemail": "Holder Email", "Holderdate Ofbirth": "Holder Date of Birth",
        "Holderdependents": "Holder Dependents", "Holderemployer": "Holder Employer",
        "Holdermarital": "Holder Marital", "Maritalstatus": "Marital Status",
        "Employername": "Employer Name", "Priorposition": "Prior Position",
        "Current Orprior Position": "Current or Prior Position",
        "Document Andcurrent Supplementalsprovided": "Document And Current Supplementals Provided",
        "Supplementalsprovided": "Supplementals Provided",
        "Numberof Yearsexperience": "Number of Years Experience",
        "Numberof Tradesper Month": "Number of Trades per Month",
        "Numberof": "Number of", "Tradesper": "Trades per", "Yearsexperience": "Years Experience",
        "Covered Callsnumbers Ofyears Experience": "Covered Calls Numbers of Years Experience",
        # Add more specific cases as identified
    }
    processed_name = name
    for pattern, replacement in specific_replacements.items():
        processed_name = re.sub(r'(?i)' + re.escape(pattern), lambda m: replacement, processed_name)
    
    # General CamelCase, number, and separator splitting
    s1 = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', processed_name)
    s1 = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', s1)
    s1 = re.sub(r'([a-zA-Z])([0-9]+)', r'\1 \2', s1)
    s1 = re.sub(r'([0-9])([a-zA-Z])', r'\1 \2', s1)
    s2 = re.sub(r'[_\-]+', ' ', s1)
    s3 = re.sub(r'\s+', ' ', s2).strip()
    
    # Capitalization with lowercase small words (of, or, per, and, etc.)
    words = s3.split(' ')
    final_words = []
    small_words = {"of", "or", "per", "and", "the", "a", "an"}
    for i, word_val in enumerate(words):
        if not word_val: continue
        if word_val.lower() in small_words and i != 0 and i != len(words) -1 :
            final_words.append(word_val.lower())
        else:
            final_words.append(word_val[0].upper() + word_val[1:])
    
    # Fix leading 'Or' if it was lowercased
    if final_words and final_words[0].lower() == "or" and name.strip().startswith("Or"):
        final_words[0] = "Or"
        
    return ' '.join(final_words)

# ------------------------------------------------
# 1. Field Extraction with Details (Includes Widget Count)
# ------------------------------------------------
def extract_fields_with_details(pdf_path):
    """Extracts detailed field info including type, options, value, and widget count."""
    detailed_fields = []
    try:
        reader = PdfReader(pdf_path)
        form_fields = reader.get_fields()
        if not form_fields:
            logger.info(f"No interactive form fields found in {pdf_path}")
            return []
            
        for field_name_fq, field_obj_ref in form_fields.items():
            try:
                field_obj = field_obj_ref.get_object()
                # Prefer /T for field name, fallback to fully qualified name
                field_name_t = field_obj.get("/T")
                if field_name_t: field_name = str(field_name_t).strip()
                else: field_name = str(field_name_fq).strip(); logger.debug(f"Field '{field_name_fq}' missing /T, using FQ name.")
                
                # --- Filtering ---
                lname = field_name.lower()
                if "signature" in lname or "datesigned" in lname or "print name" in lname or ("title" in lname and "job title" not in lname) :
                    logger.debug(f"Filtered meta field: {field_name}"); continue
                ff_val = field_obj.get("/Ff", 0)
                if isinstance(ff_val, NumberObject): ff_val = int(ff_val)
                if ff_val & PDF_FIELD_FLAG_READ_ONLY: logger.debug(f"Filtered read-only: {field_name}"); continue
                if ff_val & PDF_FIELD_FLAG_PUSHBUTTON: logger.debug(f"Ignoring pushbutton: {field_name}"); continue
                
                # --- Determine Widget Count ---
                widget_count = 0
                kids = field_obj.get(NameObject("/Kids")) # Use NameObject for keys
                if kids and isinstance(kids, ArrayObject):
                    widget_count = len(kids)
                elif NameObject("/Subtype") in field_obj and field_obj[NameObject("/Subtype")] == NameObject("/Widget"):
                    # If it has /Subtype /Widget and no /Kids, it's likely a single widget field
                     widget_count = 1
                else:
                    # Fallback: Assume 1 if it's a field pypdf returned and not clearly a non-widget parent
                    widget_count = 1 
                    logger.debug(f"Field '{field_name}' widget count determination fallback: assuming 1.")

                # --- Get Field Value ---
                raw_value = field_obj.get("/V")
                field_info_value = None
                if isinstance(raw_value, (NameObject, TextStringObject)): field_info_value = str(raw_value)
                elif raw_value is not None: field_info_value = raw_value 

                # --- Initialize Field Info Dict ---
                field_info = {
                    "name": field_name, 
                    "displayName": format_field_name_for_display(field_name),
                    "type": "text", # Default type
                    "options": [], 
                    "export_value": None, # For checkbox 'On' state
                    "value": field_info_value, 
                    "widget_count": widget_count, # Store widget count for this PDF
                    "usedInPdfs": [] # Populated later in /combined_fields
                }

                # --- Determine Field Type and Options ---
                field_type_raw = field_obj.get("/FT")
                if field_type_raw == NameObject("/Tx"): field_info["type"] = "text"
                elif field_type_raw == NameObject("/Btn"):
                    if ff_val & PDF_FIELD_FLAG_RADIO:
                        field_info["type"] = "radio"
                        radio_options = set()
                        kids_for_opts = field_obj.get("/Kids") 
                        if kids_for_opts:
                            for kid_ref in kids_for_opts:
                                kid_obj = kid_ref.get_object(); ap = kid_obj.get("/AP")
                                if ap and isinstance(ap, DictionaryObject):
                                    n_dict = ap.get("/N")
                                    if isinstance(n_dict, DictionaryObject):
                                        for evo in n_dict: 
                                            evo_str = str(evo)
                                            if evo_str.lower() != "/off": radio_options.add(evo_str)
                        if not radio_options and field_obj.get("/Opt"):
                            opts_raw = field_obj.get("/Opt")
                            if opts_raw and isinstance(opts_raw, ArrayObject):
                                for opt_val_raw in opts_raw:
                                    if isinstance(opt_val_raw, (NameObject, TextStringObject)): radio_options.add(str(opt_val_raw))
                        field_info["options"] = sorted(list(radio_options))
                        if not field_info["options"]: logger.warning(f"Radio '{field_name}' in {pdf_path} has no discernible options.")
                    else: # Checkbox
                        field_info["type"] = "checkbox"; on_value = "/Yes" 
                        ap = field_obj.get("/AP")
                        if ap and isinstance(ap, DictionaryObject):
                            n_dict = ap.get("/N")
                            if isinstance(n_dict, DictionaryObject):
                                for key_obj in n_dict: 
                                    key_str = str(key_obj)
                                    if key_str.lower() != "/off": on_value = key_str; break 
                        field_info["export_value"] = on_value
                elif field_type_raw == NameObject("/Ch"): # Choice field
                    field_info["type"] = "choice"; opts = field_obj.get("/Opt")
                    choice_options = []
                    if opts and isinstance(opts, ArrayObject):
                        for opt_item in opts:
                            if isinstance(opt_item, ArrayObject) and len(opt_item) == 2: 
                                val = str(opt_item[0]) if isinstance(opt_item[0],(TextStringObject,NameObject)) else str(opt_item[0])
                                disp = str(opt_item[1]) if isinstance(opt_item[1],(TextStringObject,NameObject)) else str(opt_item[1])
                                choice_options.append({"value": val, "display": disp})
                            elif isinstance(opt_item, (TextStringObject,NameObject)): 
                                val=str(opt_item); choice_options.append({"value":val,"display":val})
                    field_info["options"] = choice_options
                else: # Default to text if type is unknown or unhandled
                    if field_type_raw: logger.warning(f"Unknown field type '{str(field_type_raw)}' for {field_name}. Defaulting text.")
                    field_info["type"] = "text"
                
                detailed_fields.append(field_info)
            except Exception as e_inner: 
                logger.error(f"Error processing field '{str(field_name_fq)}' in {pdf_path}: {e_inner}", exc_info=False) 
    except Exception as e: 
        logger.error(f"Critical error extracting fields from {pdf_path}: {e}", exc_info=True)
    return detailed_fields

# ------------------------------------------------
# 2. PDF Data Pre-Loading
# ------------------------------------------------
pdf_data = {}
if not os.path.exists(FILES_FOLDER): 
    os.makedirs(FILES_FOLDER); logger.info(f"Created missing folder: '{FILES_FOLDER}'")
pdf_files_found = [f for f in os.listdir(FILES_FOLDER) if f.lower().endswith(".pdf")]
if not pdf_files_found:
     logger.warning(f"No PDF files found in the '{FILES_FOLDER}' directory.")
else:
    for filename in pdf_files_found:
        path = os.path.join(FILES_FOLDER, filename); logger.info(f"Loading fields from: {filename}")
        pdf_data[filename] = {"path": path, "fields_details": extract_fields_with_details(path)}
logger.info(f"Finished loading PDF data. Found {len(pdf_data)} PDF(s).")

# ------------------------------------------------
# 3. Flask Routes
# ------------------------------------------------
@app.route("/")
def serve_index(): 
    """Serves the main HTML page."""
    return app.send_static_file("index.html")

@app.route("/pdfs", methods=["GET"])
def get_pdfs_list(): 
    """Returns a list of available PDF filenames."""
    return jsonify({"pdfs": sorted(list(pdf_data.keys()))}) 

@app.route("/combined_fields", methods=["POST"])
def get_combined_fields():
    """
    Returns a list of fields based on selected PDFs and filter mode.
    - filter_mode='all': Returns all unique fields from selected PDFs.
    - filter_mode='common_only': Returns fields common across >1 selected PDF 
      OR text/choice fields appearing >1 time within a single selected PDF.
      This filter applies regardless of the number of PDFs selected.
    The list preserves the order fields were first encountered.
    """
    data = request.json
    selected_pdf_names = data.get("pdfs", [])
    filter_mode = data.get("filter_mode", "all")
    num_account_holders = int(data.get("num_account_holders", 4))
    num_financial_professionals = int(data.get("num_financial_professionals", 4))

    if not selected_pdf_names:
        return jsonify({"error": "No PDFs selected."}), 400

    combined_fields_ordered_list = []
    combined_fields_lookup = {}
    logger.debug(f"Starting combined_fields for: {selected_pdf_names}, Filter: {filter_mode}, AccHolders: {num_account_holders}, FinPros: {num_financial_professionals}")
    for pdf_name in selected_pdf_names:
        if pdf_name in pdf_data:
            for field_detail_original in pdf_data[pdf_name]["fields_details"]:
                field_detail = field_detail_original.copy() 
                field_name = field_detail["name"]
                current_pdf_widget_count = field_detail.get("widget_count", 1) 
                if field_name not in combined_fields_lookup:
                    field_detail["usedInPdfs"] = [pdf_name]
                    field_detail["max_widget_count_in_one_pdf"] = current_pdf_widget_count 
                    combined_fields_ordered_list.append(field_detail)
                    combined_fields_lookup[field_name] = field_detail
                else:
                    existing_field_entry = combined_fields_lookup[field_name]
                    if pdf_name not in existing_field_entry["usedInPdfs"]:
                        existing_field_entry["usedInPdfs"].append(pdf_name)
                    existing_max = existing_field_entry.get("max_widget_count_in_one_pdf", 0)
                    if current_pdf_widget_count > existing_max:
                        existing_field_entry["max_widget_count_in_one_pdf"] = current_pdf_widget_count
                    if field_detail["type"] == "radio" and existing_field_entry["type"] == "radio":
                        opts = set(existing_field_entry.get("options",[])); new_opts=set(field_detail.get("options",[]))
                        merged_options = sorted(list(opts.union(new_opts)))
                        if merged_options != existing_field_entry.get("options",[]):
                             existing_field_entry["options"] = merged_options
        else:
            logger.warning(f"Requested PDF '{pdf_name}' not found in preloaded data.")

    final_fields_to_display = []
    log_msg_prefix = f"PDFs: {len(selected_pdf_names)}, Filter Mode: {filter_mode}"
    
    # Initial filter based on filter_mode (common_only or all)
    pre_filtered_fields = []
    if filter_mode == "common_only":
        logger.info("Applying filter: (Common across >1 file) OR (Repeated Text/Choice in single file).")
        for field_entry in combined_fields_ordered_list:
            is_common_across_files = len(field_entry.get("usedInPdfs", [])) > 1
            field_type = field_entry.get("type", "text")
            widget_count = field_entry.get("max_widget_count_in_one_pdf", 0)
            is_repeated_data_entry_type = (widget_count > 1) and (field_type in ["text", "choice"])
            if is_common_across_files or is_repeated_data_entry_type:
                pre_filtered_fields.append(field_entry)
    else:
        pre_filtered_fields = combined_fields_ordered_list
        logger.info("Applying filter: None ('Show All' selected or <=1 PDF).")

    # Secondary filter based on number of account holders and financial professionals
    for field_entry in pre_filtered_fields:
        field_name_lower = field_entry["name"].lower()

        # Account Holder Filtering
        exclude_secondary_ah_or_current = num_account_holders < 2 and \
                                          ("secondaryaccountholder" in field_name_lower or \
                                           "secondarycurrent" in field_name_lower)
        exclude_tertiary_ah = num_account_holders < 3 and "tertiaryaccountholder" in field_name_lower
        exclude_quaternary_ah = num_account_holders < 4 and "quaternaryaccountholder" in field_name_lower
        
        if exclude_secondary_ah_or_current or exclude_tertiary_ah or exclude_quaternary_ah:
            logger.debug(f"Excluding field (AH filter): {field_entry['name']} for {num_account_holders} holders")
            continue

        # Financial Professional Filtering
        exclude_field = False
        
        # If 1 financial professional selected
        if num_financial_professionals == 1:
            if ("secondaryfinancial" in field_name_lower or
                "tertiaryfinancial" in field_name_lower or
                "quaternaryfinancial" in field_name_lower or
                "secondaryrepid" in field_name_lower or
                "tertiaryrepid" in field_name_lower or
                "quaternaryrepid" in field_name_lower):
                exclude_field = True
        
        # If 2 financial professionals selected
        elif num_financial_professionals == 2:
            if ("tertiaryfinancial" in field_name_lower or
                "quaternaryfinancial" in field_name_lower or
                "tertiaryrepid" in field_name_lower or
                "quaternaryrepid" in field_name_lower):
                exclude_field = True
        
        # If 3 financial professionals selected
        elif num_financial_professionals == 3:
            if ("quaternaryfinancial" in field_name_lower or
                "quaternaryrepid" in field_name_lower):
                exclude_field = True
        
        # If 4 financial professionals selected, don't exclude anything
        
        if exclude_field:
            logger.debug(f"Excluding field (FP filter): {field_entry['name']} for {num_financial_professionals} professionals")
            continue
            
        final_fields_to_display.append(field_entry)
    
    logger.info(f"{log_msg_prefix}, AccHolders: {num_account_holders}, FinPros: {num_financial_professionals}. Returning {len(final_fields_to_display)} fields.")
    return jsonify({"fields": final_fields_to_display})


@app.route("/fill", methods=["POST"])
def fill_and_export_pdf():
    """Fills a single specified PDF with the provided data and returns it."""
    data = request.json
    pdf_name = data.get("pdf_filename")
    field_values_from_user = data.get("field_values", {}) 
    
    if not pdf_name or pdf_name not in pdf_data: 
        return jsonify({"error": f"PDF '{pdf_name}' not found"}), 404
    
    # Special handling for CM201.pdf compatibility with account registration fields
    field_values_to_use = field_values_from_user.copy()
    if pdf_name == "CM201.pdf":
        logger.info("Applying CM201.pdf compatibility mapping for account registration fields")
        # Map account registration fields to their CM201 equivalents if needed
        for field_name, field_value in field_values_from_user.items():
            if "_RowTwo_Input" in field_name:
                continue
                
            field_name_lower = field_name.lower()
            # Skip OneTimeDistributionAmount field that was incorrectly getting mapped
            if "onetimedistribu" in field_name_lower:
                continue
                
            # Map AccountRegistration fields to appropriate CM201 fields
            # First look for direct field name matches
            if any(f["name"].lower() == field_name_lower for f in pdf_data[pdf_name]["fields_details"]):
                for cm201_field in pdf_data[pdf_name]["fields_details"]:
                    if cm201_field["name"].lower() == field_name_lower:
                        field_values_to_use[cm201_field["name"]] = field_value
                        logger.info(f"Direct map: {field_name} to {cm201_field['name']}")
            # Then try pattern matching for account registration fields
            elif "accountregistration" in field_name_lower or "account_registration" in field_name_lower:
                simple_name = field_name_lower.replace("accountregistration", "").replace("account_registration", "").strip()
                matched = False
                for cm201_field in pdf_data[pdf_name]["fields_details"]:
                    cm201_field_lower = cm201_field["name"].lower()
                    # Try to find exact matches first
                    if cm201_field_lower == simple_name:
                        field_values_to_use[cm201_field["name"]] = field_value
                        logger.info(f"Exact mapped: {field_name} to {cm201_field['name']}")
                        matched = True
                        break
                
                # If no exact match found, try suffix matching
                if not matched:
                    for cm201_field in pdf_data[pdf_name]["fields_details"]:
                        cm201_field_lower = cm201_field["name"].lower()
                        if cm201_field_lower.endswith(simple_name):
                            field_values_to_use[cm201_field["name"]] = field_value
                            logger.info(f"Suffix mapped: {field_name} to {cm201_field['name']}")
                            break
    else:
        field_values_to_use = field_values_from_user

    pdf_info = pdf_data[pdf_name]
    input_pdf_path = pdf_info["path"]
    pdf_specific_field_details_dict = {f["name"]: f for f in pdf_info["fields_details"]}
    output_stream = BytesIO()

    try:
        reader = PdfReader(input_pdf_path)
        writer = PdfWriter()
        writer.clone_reader_document_root(reader)
        
        # Process all pages from the original PDF
        for page_num in range(len(reader.pages)):
            writer.add_page(reader.pages[page_num])
        
        # Update each field with user-provided value
        for field_name, field_value in field_values_to_use.items():
            # Skip dynamically created fields that don't exist in the actual PDF
            if "_RowTwo_Input" in field_name:
                continue
                
            if field_name in pdf_specific_field_details_dict:
                field_type = pdf_specific_field_details_dict[field_name].get("type")
                
                if field_type == "checkbox":
                    # For checkboxes, we need to determine if it should be checked or not
                    on_value = pdf_specific_field_details_dict[field_name].get("export_value", "/Yes")
                    writer.update_page_form_field_values(
                        writer.pages[0], {field_name: on_value if field_value else "/Off"}
                    )
                else:
                    # For all other field types, just set the value directly
                    writer.update_page_form_field_values(
                        writer.pages[0], {field_name: field_value}
                    )
            elif field_name in reader.get_fields():
                # Field exists but we don't have detailed info about it
                writer.update_page_form_field_values(
                    writer.pages[0], {field_name: field_value}, flags=0
                )
        
        # Write the modified PDF to the output stream
        writer.write(output_stream)
        output_stream.seek(0)
        
        return send_file(
            output_stream, 
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"filled_{pdf_name}"
        )
    except Exception as e:
        logger.error(f"Error filling PDF {pdf_name}: {str(e)}", exc_info=True)
        return jsonify({"error": f"Error processing PDF: {str(e)}"}), 500

    # This duplicate code has been removed

# --- open_browser and __main__ block (for development server, should be removed/commented for deployment) ---
def open_browser():
    """Opens the web browser to the application's URL after a short delay."""
    try: 
        time.sleep(1.5) # Allow Flask server time to start
        webbrowser.open("http://127.0.0.1:5001/")
    except Exception as e: 
        logger.error(f"Could not automatically open web browser: {e}")

if __name__ == "__main__":
    # Check if the essential 'files' directory exists and has content
    if not os.path.exists(FILES_FOLDER) or not os.listdir(FILES_FOLDER):
        logger.warning(f"The '{FILES_FOLDER}' directory is missing or empty. Please create it and add PDF forms for the application to function correctly.")
    
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5001, debug=True, use_reloader=False)
