from databricks import sql
import os
import json
import base64
from dotenv import load_dotenv 
load_dotenv()

# Setup
DATABRICKS_TOKEN = os.environ.get('DATABRICKS_TOKEN')
DATABRICKS_WAREHOUSE_ID = os.environ.get('DATABRICKS_WAREHOUSE_ID')
DATABRICKS_HOST = os.environ.get('DATABRICKS_HOST', "dbc-64bffd86-32a1.cloud.databricks.com")

def get_db_connection():
    if not DATABRICKS_WAREHOUSE_ID or not DATABRICKS_TOKEN:
        raise ValueError("DATABRICKS_TOKEN or DATABRICKS_WAREHOUSE_ID is not set in environment variables.")

    connection = sql.connect(
        server_hostname=DATABRICKS_HOST.strip(),
        http_path=f"/sql/1.0/warehouses/{DATABRICKS_WAREHOUSE_ID.strip()}",
        access_token=DATABRICKS_TOKEN.strip()
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


def get_dashboard_inventory(limit: int = 10):
    """
    Fetch data from 'techbreaker_smartfridge.analysis_data.vw_dashboard_inventory_items'
    with a configurable row limit.
    Returns a list of dictionaries.
    """
    connection = None
    cursor = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Use simple LIMIT to parameterize row count
        # In Databricks / Spark SQL, LIMIT is usually supported as 'LIMIT <n>'
        query = f"SELECT * FROM techbreaker_smartfridge.analysis_data.vw_dashboard_inventory_items LIMIT {limit}"
        cursor.execute(query)
        
        # Convert result set into a list of dictionaries for easier JSON handling
        columns = [desc[0] for desc in cursor.description]
        results = []
        
        # We'll use a helper to ensure every value is JSON serializable
        def json_serial(obj):
            import datetime
            from decimal import Decimal
            if isinstance(obj, (datetime.datetime, datetime.date)):
                return obj.isoformat()
            if isinstance(obj, Decimal):
                return float(obj)
            if isinstance(obj, (bytes, bytearray)):
                # The bytes are already a base64 string (starts with /9j/), just decode to str
                try:
                    return obj.decode('utf-8')
                except UnicodeDecodeError:
                    return base64.b64encode(obj).decode('utf-8')
            try:
                import numpy as np
                if isinstance(obj, np.ndarray):   return obj.tolist()
                if isinstance(obj, np.integer):   return int(obj)
                if isinstance(obj, np.floating):  return float(obj)
            except ImportError:
                pass
            return str(obj)

        for row in cursor.fetchall():
            # Convert row to dict
            item = dict(zip(columns, row))
            
            # Deep parse known JSON fields
            json_fields = ["bbox", "dashboard_alerts"]
            for field in json_fields:
                if field in item and isinstance(item[field], str):
                    try:
                        v = item[field].strip()
                        if (v.startswith("[") and v.endswith("]")) or (v.startswith("{") and v.endswith("}")):
                            item[field] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        pass 
            
            results.append(item)
        
        # BRUTE FORCE SERIALIZATION FIX:
        # Convert to JSON and back to ensure standard Python types (no Databricks specific objects)
        safe_json_str = json.dumps(results, default=json_serial)
        safe_results = json.loads(safe_json_str)

        return {"status": "success", "data": safe_results}
        
    except Exception as e:
        print(f"Database query error: {str(e)}")
        return {"status": "error", "message": f"Failed to fetch inventory: {str(e)}"}
    
    finally:
        if cursor: cursor.close()
        if connection: connection.close()


def get_image_by_log_id(log_id: str):
    """
    Fetch the raw base64 image_data for a single log entry.
    Reads directly from image_analysis_logs (not the view) to guarantee
    the image column is present.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        query = """
            SELECT image_data
            FROM techbreaker_smartfridge.analysis_data.image_analysis_logs
            WHERE log_id = ?
            LIMIT 1
        """
        cursor.execute(query, (log_id,))
        row = cursor.fetchone()
        if row and row[0]:
            return {"status": "success", "image_data": str(row[0])}
        return {"status": "not_found", "image_data": None}
    except Exception as e:
        print(f"Image fetch error: {str(e)}")
        return {"status": "error", "message": str(e)}
    finally:
        if cursor: cursor.close()
        if connection: connection.close()


def get_dashboard_inventory_with_images(limit: int = 10):
    """
    Like get_dashboard_inventory but joins image_data from the raw table.
    Uses the view for analysis columns + joins the raw table for image bytes.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        # Join the view with the raw table to get image_data
        query = f"""
            SELECT v.*, r.image_data
            FROM techbreaker_smartfridge.analysis_data.vw_dashboard_inventory_items v
            LEFT JOIN techbreaker_smartfridge.analysis_data.image_analysis_logs r
              ON v.log_id = r.log_id
            LIMIT {limit}
        """
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        results = []

        def json_serial(obj):
            import datetime
            from decimal import Decimal
            if isinstance(obj, (datetime.datetime, datetime.date)):
                return obj.isoformat()
            if isinstance(obj, Decimal):
                return float(obj)
            if isinstance(obj, (bytes, bytearray)):
                # The bytes are already a base64 string (starts with /9j/), just decode to str
                try:
                    return obj.decode('utf-8')
                except UnicodeDecodeError:
                    return base64.b64encode(obj).decode('utf-8')
            try:
                import numpy as np
                if isinstance(obj, np.ndarray):   return obj.tolist()
                if isinstance(obj, np.integer):   return int(obj)
                if isinstance(obj, np.floating):  return float(obj)
            except ImportError:
                pass
            return str(obj)

        for row in cursor.fetchall():
            item = dict(zip(columns, row))
            json_fields = ["bbox", "dashboard_alerts"]
            for field in json_fields:
                if field in item and isinstance(item[field], str):
                    try:
                        v = item[field].strip()
                        if (v.startswith("[") and v.endswith("]")) or (v.startswith("{") and v.endswith("}")):
                            item[field] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(item)

        safe_json_str = json.dumps(results, default=json_serial)
        safe_results = json.loads(safe_json_str)
        return {"status": "success", "data": safe_results}

    except Exception as e:
        print(f"Database query error (with images): {str(e)}")
        # Fallback to the original function without images
        return get_dashboard_inventory(limit=limit)
    finally:
        if cursor: cursor.close()
        if connection: connection.close()
