
'''from fastapi import FastAPI
from llm_router import ask
from notifier import notify_user
import os

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "AI Assistant is online."}

@app.get("/ask")
def ask_question(q: str):
    answer = ask(q)
    notify_user(f"You asked: {q}\nAssistant: {answer}")
    return {"answer": answer}

# CLI interface
if __name__ == "__main__":
    while True:
        q = input("Ask the assistant: ")
        if q.lower() in ["exit", "quit"]:
            break
        print("Thinking...")
        a = ask(q)
        print("Assistant:", a)'''
from fastapi import FastAPI
from llm_router import ask
from notifier import notify_user

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "AI Assistant is online."}

@app.get("/ask")
def ask_question(q: str):
    answer = ask(q)
    notify_user(f"You asked: {q}\nAssistant: {answer}")
    return {"answer": answer}
