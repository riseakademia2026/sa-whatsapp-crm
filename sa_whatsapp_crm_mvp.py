from flask import Flask, request
import os

app = Flask(__name__)

VERIFY_TOKEN = "my_verify_token_123"

@app.route("/")
def home():
    return "SA WhatsApp CRM is running"

@app.route("/webhook", methods=["GET", "POST"])
def webhook():

    if request.method == "GET":

        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200

        return "Verification failed", 403

    if request.method == "POST":

        data = request.get_json()

        print("Incoming WhatsApp Webhook:")
        print(data)

        return "OK", 200

    return "OK", 200


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )