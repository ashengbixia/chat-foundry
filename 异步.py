import httpx
from fastapi import FastAPI,HTTPException
app=FastAPI

@app.get("/external")
async def get_external():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response=await client.get("https//httpbin.org/get")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504,detail="超时")
    except httpx.HTTPError:
        raise HTTPException(status_code=504,detail="异常")
