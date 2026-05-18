
from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return """
    <h1>SA WhatsApp CRM MVP</h1>
    <p>系统已经成功运行。</p>
    <p>下一步：</p>
    <ol>
        <li>填入 WhatsApp Access Token</li>
        <li>填入 Phone Number ID</li>
        <li>接 Webhook</li>
    </ol>
    """

if __name__ == "__main__":
    app.run(debug=True)
