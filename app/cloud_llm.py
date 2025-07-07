
import openai
import os

openai.api_key = os.getenv("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")

def ask_cloud(prompt: str) -> str:
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[Cloud Error] {str(e)}"
