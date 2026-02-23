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
    
@app.get("/")
def read_root():
    return {"Hello": "World"}

class ImagePayload(BaseModel):  
    image: str  # The Base64 string
    metadata: dict  # Your custom request info (param1, etc.)
    company_id: str = "techbreakerllc"  # Optional company identifier
    machine_id: str   # Optional machine identifier
    camera_id: str   # Optional camera identifier,1 friger can have multiple cameras, so we can use a list to store multiple camera ids.

def get_mime_type(base64_bytes: str):
    # Decode the base64 to check the header
    import base64
    img_data = base64.b64decode(base64_bytes)
    
    # Use PIL to identify the image format
    with Image.open(io.BytesIO(img_data)) as img:
        fmt = img.format.lower() # returns 'jpeg', 'png', 'webp', etc.
        return f"image/{fmt}"
def llm_call(base64_image:bytes,config,provider:str):
    #  system_prompt=load_config()['system_prompt']
    model=config["llm_model"][provider]["name"]
    system_prompt=config["prompt_template"]["system_prompt"]
    analysis_prompt=config["prompt_template"]["analysis_prompt"]
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
@app.post("/analyze")
async def analyze_image(payload: ImagePayload, request: Request):
    # 1. Decode the image from the Pydantic model
    image_bytes = base64.b64decode(payload.image)
    
    # 2. Extract browser/client info from the live Request object
    browser_info = request.headers.get("user-agent")
    client_ip = request.client.host
    llm_response = llm_call(payload.image, config, "openai")
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
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)