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
    Insert image analysis results into the database.
    
    Expected db_insert_data dictionary:
    {
        "llm_response": str,
        "bytes_len": int,
        "image_bytes": str (base64),
        "custom_metadata": dict,
        "company_id": str,
        "machine_id": str,
        "camera_id": str,
        "headers": str,
        "client_ip": str
    }
    """
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Convert custom_metadata dict to JSON string
        custom_metadata_json = json.dumps(db_insert_data.get("custom_metadata", {}))
        
        # Convert llm_response to string if it's an object or dict
        llm_response = db_insert_data.get("llm_response")
        llm_metadata = {}
        
        if isinstance(llm_response, dict):
            # Extract content and metadata from dict response
            llm_content = llm_response.get("content", "")
            llm_metadata = {
                "model": llm_response.get("model"),
                "prompt_tokens": llm_response.get("prompt_tokens"),
                "completion_tokens": llm_response.get("completion_tokens"),
                "total_tokens": llm_response.get("total_tokens"),
                "finish_reason": llm_response.get("finish_reason")
            }
            llm_response_str = llm_content
        elif hasattr(llm_response, 'content'):
            # Handle ChatCompletionMessage object
            llm_response_str = llm_response.content
        elif not isinstance(llm_response, str):
            llm_response_str = str(llm_response)
        else:
            llm_response_str = llm_response
        
        # Store llm metadata as JSON string
        llm_metadata_json = json.dumps(llm_metadata)
        
        insert_query = """
        INSERT INTO techbreaker_smartfridge.analysis_data.image_analysis_logs 
        (llm_response, bytes_len, image_data, custom_metadata, company_id, machine_id, camera_id, headers, client_ip, created_timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        
        # Store llm_response content with metadata as a combined JSON
        llm_combined = {
            "content": llm_response_str,
            "metadata": llm_metadata
        }
        llm_combined_json = json.dumps(llm_combined)
        
        cursor.execute(insert_query, (
            llm_combined_json,
            db_insert_data.get("bytes_len"),
            db_insert_data.get("image_bytes"),  # Base64 string stored as BINARY
            custom_metadata_json,
            db_insert_data.get("company_id"),
            db_insert_data.get("machine_id"),
            db_insert_data.get("camera_id"),
            db_insert_data.get("headers"),
            db_insert_data.get("client_ip")
        ))
        
        connection.commit()
        
        return {"status": "success", "message": "Analysis result inserted successfully"}
    
    except json.JSONDecodeError as e:
        print(f"JSON encoding error: {str(e)}")
        return {"status": "error", "message": f"Failed to encode metadata to JSON: {str(e)}"}
    
    except Exception as e:
        print(f"Database insertion error: {str(e)}")
        if connection:
            try:
                connection.rollback()
            except:
                pass
        return {"status": "error", "message": f"Failed to insert analysis result: {str(e)}"}
    
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if connection:
            try:
                connection.close()
            except:
                pass