from fastapi import FastAPI
import requests,base64

url=" http://localhost:8000"
endpoint="/analyze"

def test_analyze_image():
    import os
    image_path = os.path.join(os.path.dirname(__file__), 'image_1770601155.jpg')
        
    with open(image_path, 'rb') as f:
        encoded_image = base64.b64encode(f.read()).decode('utf-8')

    payload = {
        "image": encoded_image,
        "metadata": {"param1": "value1"},
        "company_id": "techbreakerllc",
        "machine_id": "fridge_002",
        "camera_id": ["cam_001"]
    }

    # The 'json=' parameter handles the Content-Type header for you
    response = requests.post(url+endpoint, json=payload)
    
    # Debug: Check status code and response
    print(f"Status Code: {response.status_code}")
    print(f"Response Text: {response.text}")
    
    if response.status_code == 200:
        print(response.json())
    else:
        print(f"Error: {response.text}")
if __name__ == "__main__":
    result = test_analyze_image()
    print("Response from API:", result)