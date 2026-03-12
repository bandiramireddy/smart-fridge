from http import client
import os
import sys
from pathlib import Path
import base64
from pydantic import BaseModel
from openai import OpenAI
from PIL import Image
import io
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi  import FastAPI, Request
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
from config import load_config
config=load_config()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include data retrieval endpoints
# from fastapi_backend.get_data import router as data_router
# app.include_router(data_router)
    
@app.get("/")
def read_root():
    return {"Hello": "World"}

class ImagePayload(BaseModel):  
    image: str          # The Base64 string
    metadata: dict      # Your custom request info (param1, etc.)
    company_id: str = "techbreakerllc"  # Optional company identifier
    machine_id: str     # Optional machine identifier
    camera_id: str      # Optional camera identifier — 1 fridge can have multiple cameras
    model: str = None   # Optional: override the model (used by /analyze_openrouter)

def get_mime_type(base64_bytes: str):
    # Decode the base64 to check the header
    import base64
    img_data = base64.b64decode(base64_bytes)
    
    # Use PIL to identify the image format
    with Image.open(io.BytesIO(img_data)) as img:
        fmt = img.format.lower() # returns 'jpeg', 'png', 'webp', etc.
        return f"image/{fmt}"
def llm_call(base64_image:bytes,metadata:dict,config,provider:str):
    #  system_prompt=load_config()['system_prompt']
    model=config["llm_model"][provider]["name"]
    system_prompt=config["prompt_template"]["system_prompt"]
    analysis_prompt=config["prompt_template"]["analysis_prompt"].replace("{{temperature_value}}", str(metadata.get("temperature", "unknown")))
    max_tokens=config["llm_model"][provider]["max_tokens"]
    if provider=="openai":
        client = OpenAI()
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": analysis_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/{get_mime_type(base64_image)};base64,{base64_image}"}}
                ]
            }
        ]
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=max_tokens
            )
            msg = response.choices[0].message
            if hasattr(msg, "refusal") and msg.refusal:
                raise ValueError(f"Model refused: {msg.refusal}")
            
            # Return full response details with token counts and model info
            return {
                "content": msg.content,
                "model": response.model,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "finish_reason": response.choices[0].finish_reason
            }
        except Exception as e:
            if "is only supported by certain models" in str(e):
                raise ValueError(f"Model '{model}' does not support image URLs. Use gpt-4-vision, gpt-4-turbo, or gpt-4o instead.") from e
            raise

# import base64
# import json

def llm_openrouter_call(base64_image: bytes, metadata: dict, config, provider: str, model_override: str = None):
    """
    Call OpenRouter API with a dynamically selected model.
    
    - model_override: if provided (e.g. sent from the HTML frontend), this model
      is used directly. Otherwise falls back to config[provider]['name'].
    """
    system_prompt = config["prompt_template"]["system_prompt"]
    analysis_prompt = config["prompt_template"]["analysis_prompt"].replace(
        "{{temperature_value}}", str(metadata.get("temperature", "0"))
    )
    max_tokens = config[provider]["max_tokens"]

    # ── Model resolution ─────────────────────────────────────────────────────
    # If the frontend sent a specific model ID (e.g. "openai/gpt-5"),
    # use it directly. Otherwise fall back to the config default.
    if model_override:
        # Strip leading "openrouter/" prefix if the frontend accidentally added it
        model = model_override.lstrip("openrouter/") if model_override.startswith("openrouter/openrouter/") else model_override
    else:
        model = config[provider]["name"]   # e.g. "qwen/qwen3-235b-a22b"
    # ─────────────────────────────────────────────────────────────────────────
    
    # 1. Construct the Multimodal Messages
    # Convert image bytes to string if it isn't already
    if isinstance(base64_image, bytes):
        base64_image_str = base64_image.decode('utf-8')
    else:
        base64_image_str = base64_image

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": analysis_prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image_str}"
                    }
                }
            ]
        }
    ]

    if provider == "openrouter":
        OPENROUTER_API_KEY=os.getenv("OPENROUTER_API_KEY")
        client = OpenAI(base_url="https://openrouter.ai/api/v1",api_key=OPENROUTER_API_KEY )
        try:
            # 2. API Call with Reasoning enabled via extra_body
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=max_tokens,
                # This enables the 'thinking'/reasoning tokens
                extra_body={
                    "reasoning": {"enabled": True}
                }
            )
            
            msg = response.choices[0].message
            
            if hasattr(msg, "refusal") and msg.refusal:
                raise ValueError(f"Model refused: {msg.refusal}")
            
            # 3. Extract reasoning if available
            # OpenRouter often puts reasoning in 'reasoning_details' or 'reasoning'
            reasoning = getattr(msg, "reasoning", None)
            
            return {
                "content": msg.content,
                "reasoning": reasoning,
                "model": response.model,          # actual model used (from OpenRouter response)
                "model_requested": model,         # what we sent in the request
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "finish_reason": response.choices[0].finish_reason,
                # "usage":response.usage,
                "cost": response.usage.cost,
            }
            
        except Exception as e:
            if "is only supported by certain models" in str(e):
                raise ValueError(
                    f"Model '{model}' does not support images. "
                    "Try 'openrouter/free' or a specific vision model."
                ) from e
            raise e
@app.post("/analyze")
async def analyze_image(payload: ImagePayload, request: Request):
    # 1. Decode the image from the Pydantic model
    image_bytes = base64.b64decode(payload.image)
    
    # 2. Extract browser/client info from the live Request object
    browser_info = request.headers.get("user-agent")
    client_ip = request.client.host
    llm_response = llm_call(payload.image,payload.metadata, config, "openai")
    db_insert_data = {
        "llm_response": llm_response,
        "bytes_len": len(image_bytes),
        "image_bytes": payload.image,  # Base64 string (not decoded bytes)
        "custom_metadata": payload.metadata,
        "company_id": payload.company_id,
        "machine_id": payload.machine_id,
        "camera_id": payload.camera_id,
        "headers": str(request.headers),
        "client_ip": client_ip,
    }
    # print("DB Insert Data:", db_insert_data)  # Debug: Check the data before insertion
    from db.db_operations import insert_analysis_result
    insert_result = insert_analysis_result(db_insert_data)
    print("DB Insert Result:", insert_result)  # Debug: Check the result of DB insertion
    return {
        "message": "Image decoded and request captured",
        "request": {
            "bytes_len": len(image_bytes),
            "custom_metadata": payload.metadata,
            "company_id": payload.company_id,
            "machine_id": payload.machine_id,
            "camera_id": payload.camera_id,
            "browser": browser_info,
            "ip": client_ip
        },
        "llm_response": llm_response
    }
@app.post("/analyze_openrouter")
async def analyze_image_openrouter(payload: ImagePayload, request: Request):
    # 1. Decode the image from the Pydantic model
    image_bytes = base64.b64decode(payload.image)
    
    # 2. Extract browser/client info from the live Request object
    browser_info = request.headers.get("user-agent")
    client_ip = request.client.host

    # 3. Call OpenRouter — pass payload.model so the frontend can pick any model
    llm_response = llm_openrouter_call(
        payload.image,
        payload.metadata,
        config,
        "openrouter",
        model_override=payload.model   # ← dynamic model from HTML frontend
    )

    db_insert_data = {
        "llm_response": llm_response,
        "bytes_len": len(image_bytes),
        "image_bytes": payload.image,
        "custom_metadata": payload.metadata,
        "usage": llm_response.get("usage", {}), #Usage include tokesn and cost
        "company_id": payload.company_id,
        "machine_id": payload.machine_id,
        "camera_id": payload.camera_id,
        "headers": str(request.headers),
        "client_ip": client_ip,
    }
    from db.db_operations import insert_analysis_result
    insert_result = insert_analysis_result(db_insert_data)
    print("DB Insert Result:", insert_result)
    return {
        "message": "Image decoded and request captured",
        "request": {
            "bytes_len": len(image_bytes),
            "custom_metadata": payload.metadata,
            "company_id": payload.company_id,
            "machine_id": payload.machine_id,
            "camera_id": payload.camera_id,
            "browser": browser_info,
            "ip": client_ip,
            "model_requested": payload.model,   # echo back so frontend can verify
        },
        "llm_response": llm_response
    }
from fastapi import APIRouter, Query
from db.db_operations import get_dashboard_inventory, get_image_by_log_id, get_dashboard_inventory_with_images

# router = APIRouter()

@app.get("/dashboard/inventory")
async def fetch_inventory(limit: int = Query(10, description="Number of rows to return")):
    """
    Fetch dashboard inventory items including image_data from Databricks.
    Tries the JOIN query first (view + raw table); falls back to view-only on error.
    """
    result = get_dashboard_inventory_with_images(limit=limit)
    return result


@app.get("/dashboard/image/{log_id}")
async def fetch_image(log_id: str):
    """
    Fetch the base64 image for a single log entry by its log_id.
    Used by the dashboard to lazy-load images without embedding in the inventory payload.
    """
    result = get_image_by_log_id(log_id)
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
