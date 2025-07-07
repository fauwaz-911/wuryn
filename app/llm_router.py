
from local_llm import ask_local
from cloud_llm import ask_cloud
import socket

def is_connected():
    try:
        socket.create_connection(("1.1.1.1", 53))
        return True
    except:
        return False

def ask(prompt: str) -> str:
    if is_connected():
        return ask_cloud(prompt)
    else:
        return ask_local(prompt)
