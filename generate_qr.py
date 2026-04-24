import qrcode

url = "https://jk-tourism.onrender.com/register"

img = qrcode.make(url)
img.save("entry_qr.png")

print("QR created successfully!")