from databricks import sql
import os
import json
from dotenv import load_dotenv 
# Setup
DATABRICKS_TOKEN = os.environ.get('DATABRICKS_TOKEN')
DATABRICKS_WAREHOUSE_ID = os.environ.get('DATABRICKS_WAREHOUSE_ID')
DATABRICKS_HOST = "dbc-64bffd86-32a1.cloud.databricks.com"

def get_db_connection():
    connection = sql.connect(
        server_hostname=DATABRICKS_HOST,
        http_path=f"/sql/1.0/warehouses/{DATABRICKS_WAREHOUSE_ID}",
        access_token=DATABRICKS_TOKEN
    )
    return connection


def insert_analysis_result(db_insert_data):
    """
    Insert image analysis results into the database with deep JSON parsing.
    """
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # 1. Prepare Custom Metadata
        custom_metadata_json = json.dumps(db_insert_data.get("custom_metadata", {}))
        
        # 2. Process the LLM Response
        llm_input = db_insert_data.get("llm_response", {})
        llm_metadata = {}
        llm_analysis_payload = None

        if isinstance(llm_input, dict):
            # Capture the outer metadata (tokens, model, etc.)
            llm_metadata = {
                "model": llm_input.get("model"),
                "prompt_tokens": llm_input.get("prompt_tokens"),
                "completion_tokens": llm_input.get("completion_tokens"),
                "total_tokens": llm_input.get("total_tokens"),
                "cost": llm_input.get("cost"),
                "finish_reason": llm_input.get("finish_reason")
            }
            
            # Extract and Parse the inner 'content' string
            raw_content = llm_input.get("content", "")
            try:
                # This turns the "total_items": 3 string into a queryable dict
                llm_analysis_payload = json.loads(raw_content)
            except (json.JSONDecodeError, TypeError):
                # Fallback if content isn't actually JSON
                llm_analysis_payload = raw_content

        # Combine into one clean object for the Delta Table
        llm_combined_json = json.dumps({
            "analysis": llm_analysis_payload,
            "metadata": llm_metadata
        })
        
        # 3. Database Insertion
        insert_query = """
        INSERT INTO techbreaker_smartfridge.analysis_data.image_analysis_logs 
        (llm_response, bytes_len, image_data, custom_metadata, company_id, machine_id, camera_id, headers, client_ip, created_timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        
        cursor.execute(insert_query, (
            llm_combined_json,
            db_insert_data.get("bytes_len"),
            db_insert_data.get("image_bytes"),
            custom_metadata_json,
            db_insert_data.get("company_id"),
            db_insert_data.get("machine_id"),
            db_insert_data.get("camera_id"),
            db_insert_data.get("headers"),
            db_insert_data.get("client_ip")
        ))
        
        connection.commit()
        return {"status": "success", "message": "Analysis result inserted successfully"}
    
    except Exception as e:
        if connection:
            connection.rollback()
        print(f"Database insertion error: {str(e)}")
        return {"status": "error", "message": f"Failed to insert: {str(e)}"}
    
    finally:
        if cursor: cursor.close()
        if connection: connection.close()