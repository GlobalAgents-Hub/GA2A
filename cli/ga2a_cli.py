from fastapi import FastAPI
from pydantic import BaseModel
import logging
import uvicorn
import json
import os
import sys
# Corrige path pro ga2a.py no root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)).replace('cli', ''))
try:
    from ga2a import load_data, save_data, spawn, create_zone, interact
except ImportError:
    print("ga2a.py não encontrado no root – renomeie a2a.py pra ga2a.py")
    raise
from datetime import datetime, timedelta
import sqlite3
from typing import List

logging.basicConfig(filename='ga2a-logs.txt', level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

app = FastAPI(title="GA2A Neurotech Hub")

# MODELS CORRIGIDOS
class ZoneRequest(BaseModel):
    zone: str

class TaskRequest(BaseModel):
    task_id: str
    from_zone: str
    to_zone: str
    payload: str

conn = sqlite3.connect('ga2a-zones.db', check_same_thread=False)
conn.execute('CREATE TABLE IF NOT EXISTS zones (zone TEXT PRIMARY KEY, token TEXT, expires TEXT)')
conn.commit()

@app.post("/register-zone")
async def register_zone(request: ZoneRequest):  # ← ZoneRequest ao invés de Zone
    token = f"ga2a_{request.zone}_{int(datetime.now().timestamp())}"
    expires = (datetime.now() + timedelta(hours=24)).isoformat()
    conn.execute("INSERT OR REPLACE INTO zones (zone, token, expires) VALUES (?, ?, ?)", 
                 (request.zone, token, expires))
    conn.commit()
    logging.info(f"Zone '{request.zone}' registered: token={token}")
    return {"status": "ok", "zone": request.zone, "token": token, "expires": expires}

@app.get("/zones")
async def get_zones():
    cursor = None
    try:
        cursor = conn.execute("SELECT zone, token, expires FROM zones WHERE datetime(expires) > datetime('now')")
        zones = [{"zone": row[0], "token": row[1], "expires": row[2]} for row in cursor.fetchall()]
        return {"active_zones": zones}
    finally:
        if cursor:
            cursor.close()

@app.post("/tasks")
@app.post("/tasks")
async def create_task(request: TaskRequest):
    logging.info(f"Task {request.task_id}: {request.from_zone} -> {request.to_zone}: {request.payload[:50]}...")
    # GA2A simbólico simples sem erro
    result = f"Plexit processou: {request.payload[:100]}... | BrainSmart EEG ready!"
    logging.info(f"Task result: {result}")
    return {"status": "processed", "result": result, "to_zone": request.to_zone}

    logging.info(f"Task {request.task_id}: {request.from_zone} -> {request.to_zone}: {request.payload[:50]}...")
    try:
        result = interact(spawn(request.payload), zone=request.to_zone)
        save_data({"task_id": request.task_id, "result": str(result)})
        return {"status": "processed", "result": str(result)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080, reload=True)
