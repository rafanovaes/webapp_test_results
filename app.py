import pandas as pd
from flask import Flask, render_template, request, jsonify, session
import io
import re

app = Flask(__name__)
# IMPORTANT: Change this to a strong, random key in production!
# You can generate one with: import os; os.urandom(24)
app.secret_key = 'your_super_secret_key_please_change_this_for_production'

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload_csv', methods=['POST'])
def upload_csv():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if file:
        try:
            # Read CSV into a pandas DataFrame
            df = pd.read_csv(io.StringIO(file.read().decode('utf-8')))

            # Validate expected columns
            required_columns = ['Test Name', 'Test Point Name', 'Criteria Type', 'Acceptance Criteria']
            if not all(col in df.columns for col in required_columns):
                missing = [col for col in required_columns if col not in df.columns]
                return jsonify({'error': f'Missing required CSV columns: {", ".join(missing)}. Required: {", ".join(required_columns)}'}), 400
            
            # Ensure 'Test Point Name' is unique as it will be the key for results
            if df['Test Point Name'].duplicated().any():
                duplicates = df[df['Test Point Name'].duplicated()]['Test Point Name'].tolist()
                return jsonify({'error': f'Duplicate "Test Point Name" found in CSV: {", ".join(set(duplicates))}. Each Test Point Name must be unique.'}), 400

            # Store the processed test data in the session, keyed by 'Test Point Name'
            tests_data_dict = df.set_index('Test Point Name').to_dict(orient='index')
            session['tests_data'] = tests_data_dict

            # Prepare data to send back to frontend, grouped by 'Test Name' for display
            display_tests_grouped = {}
            for index, row in df.iterrows():
                test_name = row['Test Name']
                test_point_name = row['Test Point Name']
                units = row.get('Units', '') # Use .get() for optional 'Units' column

                if test_name not in display_tests_grouped:
                    display_tests_grouped[test_name] = []
                
                display_tests_grouped[test_name].append({
                    'Test Point Name': test_point_name,
                    'Units': units
                })
            
            return jsonify({'success': True, 'tests_grouped': display_tests_grouped})

        except Exception as e:
            app.logger.error(f"Error processing CSV: {e}", exc_info=True)
            return jsonify({'error': f'Error processing CSV file: {str(e)}'}), 500
    return jsonify({'error': 'File upload failed.'}), 400


@app.route('/submit_results', methods=['POST'])
def submit_results():
    user_results = request.json.get('results', {}) # results will be keyed by Test Point Name
    tests_data = session.get('tests_data') # This is now a dictionary keyed by Test Point Name

    if not tests_data:
        return jsonify({'error': 'No test data found in session. Please upload a CSV first.'}), 400

    results_summary = []

    for test_point_name, test_details in tests_data.items(): # Iterate through stored test points
        test_name = test_details['Test Name'] # Get the main test name for display
        criteria_type = test_details['Criteria Type']
        acceptance_criteria_str = str(test_details['Acceptance Criteria']).strip()
        user_result_str = user_results.get(test_point_name, '').strip() # Get result for THIS test point
        
        status = 'FAIL' # Default status
        message = ''

        try:
            if criteria_type == 'text_match':
                if user_result_str.lower() == acceptance_criteria_str.lower():
                    status = 'PASS'
                message = f'Expected: "{acceptance_criteria_str}", Got: "{user_result_str}"'
            else: # For all numerical criteria types
                try:
                    user_val = float(user_result_str) # Attempt to convert user input to float
                except ValueError:
                    raise ValueError(f'Result "{user_result_str}" is not a valid number.') # Raise for specific handling below
                
                if criteria_type == 'range':
                    # Expects something like "20-25"
                    match = re.match(r'(\d+(\.\d+)?)-(\d+(\.\d+)?)', acceptance_criteria_str)
                    if match:
                        min_val = float(match.group(1))
                        max_val = float(match.group(3))
                        if min_val <= user_val <= max_val:
                            status = 'PASS'
                        message = f'Expected: {min_val}-{max_val}, Got: {user_val}'
                    else:
                        message = f'Invalid range format: {acceptance_criteria_str}. Expected "MIN-MAX".'
                        status = 'ERROR'
                elif criteria_type == 'greater_than':
                    # Expects a number like "100"
                    required_val = float(acceptance_criteria_str)
                    if user_val > required_val:
                        status = 'PASS'
                    message = f'Expected: > {required_val}, Got: {user_val}'
                elif criteria_type == 'less_than':
                    # Expects a number like "7.5"
                    required_val = float(acceptance_criteria_str)
                    if user_val < required_val:
                        status = 'PASS'
                    message = f'Expected: < {required_val}, Got: {user_val}'
                elif criteria_type == 'equal':
                    try:
                        required_val = float(acceptance_criteria_str)
                        if user_val == required_val:
                            status = 'PASS'
                        message = f'Expected: == {required_val}, Got: {user_val}'
                    except ValueError: # Fallback to string comparison if criteria isn't a number
                         if user_result_str.lower() == acceptance_criteria_str.lower():
                            status = 'PASS'
                         message = f'Expected: == "{acceptance_criteria_str}", Got: "{user_result_str}"'
                elif criteria_type == 'nominal_tolerance':
                    # Expects "nominal_value,tolerance" or "nominal_value-tolerance"
                    parts = acceptance_criteria_str.split(',') # Try splitting by comma
                    if len(parts) != 2:
                        parts = acceptance_criteria_str.split('-') # Or by dash
                    
                    if len(parts) == 2:
                        try:
                            nominal_val = float(parts[0].strip())
                            tolerance = float(parts[1].strip())
                        except ValueError:
                            raise ValueError(f'Nominal or tolerance value "{acceptance_criteria_str}" is not a valid number.')
                        
                        min_val = nominal_val - tolerance
                        max_val = nominal_val + tolerance

                        if min_val <= user_val <= max_val:
                            status = 'PASS'
                        message = f'Expected: {nominal_val} +/- {tolerance} ({min_val:.2f}-{max_val:.2f}), Got: {user_val}'
                    else:
                        message = f'Invalid nominal_tolerance format: {acceptance_criteria_str}. Expected "nominal,tolerance" or "nominal-tolerance".'
                        status = 'ERROR'
                else:
                    message = f'Unknown criteria type: {criteria_type}'
                    status = 'ERROR'

        except ValueError as ve: # Catches errors from float(user_result_str) or nominal/tolerance conversion
            message = f'Invalid Input: {str(ve)}'
            status = 'INVALID INPUT'
        except Exception as e:
            app.logger.error(f"Unexpected error in submit_results for {test_point_name}: {e}", exc_info=True)
            message = f'An unexpected error occurred: {str(e)}'
            status = 'ERROR'

        results_summary.append({
            'Test Name': test_name,         # Main test name
            'Test Point Name': test_point_name, # Specific test point
            'User Result': user_result_str,
            'Status': status,
            'Message': message
        })

    return jsonify({'success': True, 'results': results_summary})

if __name__ == '__main__':
    app.run(debug=True) # debug=True allows for auto-reloading and helpful error messages
