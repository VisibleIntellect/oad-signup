"""
Make a QR code that points to your public availability board.

Usage:
    python3 make_qr.py https://your-app-name.onrender.com

It saves "availability_qr.png" in this folder. Print it or show it at the
registration table -- scanning it opens the live "what's available" board.
"""
import sys
import subprocess

def main():
    if len(sys.argv) < 2:
        print('Usage: python3 make_qr.py <your-public-url>')
        print('Example: python3 make_qr.py https://oad-availability.onrender.com')
        sys.exit(1)
    url = sys.argv[1].strip()

    try:
        import qrcode
    except ImportError:
        print("Installing the QR library (one time)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "qrcode[pil]"], check=True)
        import qrcode

    qr = qrcode.QRCode(box_size=12, border=3,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#234f2f", back_color="white")
    out = "availability_qr.png"
    img.save(out)
    print(f"\nSaved {out}")
    print(f"It links to: {url}")
    print("Print it or display it at your registration table.")

if __name__ == "__main__":
    main()
