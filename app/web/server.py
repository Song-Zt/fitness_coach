"""Uvicorn 入口：uvicorn app.web.server:app --port 8000"""
import uvicorn

from app.web.api import create_app

app = create_app()



if __name__ == '__main__':
    uvicorn.run(app, host="127.0.0.1", port=8000)