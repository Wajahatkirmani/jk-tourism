import qrcode

url = "https://jk-tourism.onrender.com/register"

img = qrcode.make(url)
img.save("ENTRY_QR.png")

print("QR created successfully!")