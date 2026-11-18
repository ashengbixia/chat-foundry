from fastapi import FastAPI
from pydantic import BaseModel
app=FastAPI()
ids=1
devices={}
class divice(BaseModel):
    name:str
    temprature:float

@app.post('/device',status_code=201)
def create_device(data:divice):
    global ids
    ids=ids+1
    device={"id":ids,"data":data.model_dump()}
    devices[ids]=device
    return devices

