
import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

def ask_cloud(prompt: str) -> str:
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[Cloud Error] {str(e)}"
