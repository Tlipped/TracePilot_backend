import uvicorn
from app.main import app


if __name__ == "__main__":
    print("Starting TracePilot Web Server...")
    print("Server will be available at http://localhost:8000")
    print("API documentation available at http://localhost:8000/docs")
    
    # 启动FastAPI服务器
    uvicorn.run(app, host="0.0.0.0", port=8000)