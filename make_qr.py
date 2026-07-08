"""
Make a branded QR code that points to your public availability board.

Usage:
    python3 make_qr.py https://your-app-name.onrender.com

It saves "availability_qr.png" in this folder — a FOBBV-green QR with the eagle/
bear logo set into the center. Print it or show it at the registration table;
scanning it opens the live "what's available" board.
"""
import sys
import os
import subprocess


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 make_qr.py <your-public-url>')
        print('Example: python3 make_qr.py https://oad-availability.onrender.com')
        sys.exit(1)
    url = sys.argv[1].strip()

    try:
        import qrcode
        from PIL import Image, ImageFilter
    except ImportError:
        print("Installing the QR library (one time)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "qrcode[pil]"],
                       check=True)
        import qrcode
        from PIL import Image, ImageFilter

    # High error correction so the centered logo stays scannable.
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H,
                       box_size=16, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1f4e31", back_color="white").convert("RGBA")
    W, H = img.size

    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "static", "logo.png")
    try:
        logo = Image.open(logo_path).convert("RGBA")
        target = int(W * 0.45)
        lw, lh = logo.size
        s = target / max(lw, lh)
        logo = logo.resize((max(1, int(lw * s)), max(1, int(lh * s))),
                           Image.LANCZOS)
        lw, lh = logo.size
        halo = logo.split()[3].point(lambda a: 255 if a > 40 else 0)
        for _ in range(3):
            halo = halo.filter(ImageFilter.MaxFilter(5))
        pos = ((W - lw) // 2, (H - lh) // 2)
        img.paste(Image.new("RGBA", (lw, lh), (255, 255, 255, 255)), pos, halo)
        img.paste(logo, pos, logo)
    except FileNotFoundError:
        print("(No logo at static/logo.png — generating a plain QR.)")

    out = "availability_qr.png"
    img.convert("RGB").save(out)
    print(f"\nSaved {out}")
    print(f"It links to: {url}")
    print("Print it or display it at your registration table.")


if __name__ == "__main__":
    main()
