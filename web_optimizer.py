import threading
import webbrowser
import time
import io
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:
    tk = None
from flask import Flask, request, render_template_string, send_from_directory

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pillow_avif
except ImportError:
    pillow_avif = None

def get_sort_key(path):
    """Extracts the first number in a filename for numeric sorting."""
    nums = re.findall(r'\d+', path.name)
    return (0, int(nums[0])) if nums else (1, path.name)

# --- Image Processing Logic ---

def parse_aspect_ratio(ar_str):
    if not ar_str:
        return None
    try:
        w, h = map(float, ar_str.split(':'))
        return w / h
    except ValueError:
        raise ValueError("Aspect ratio must be 'W:H'")

def crop_to_aspect_ratio(image, target_ratio):
    orig_w, orig_h = image.size
    orig_ratio = orig_w / orig_h
    if abs(orig_ratio - target_ratio) < 0.01:
        return image
    if orig_ratio > target_ratio:
        new_w = int(orig_h * target_ratio)
        left = (orig_w - new_w) // 2
        return image.crop((left, 0, left + new_w, orig_h))
    else:
        new_h = int(orig_w / target_ratio)
        top = (orig_h - new_h) // 2
        return image.crop((0, top, orig_w, top + new_h))

def process_image(input_source, output_path, max_width, quality, target_ratio=None, watermark_text=None, wm_pos='bottom-right', wm_size=3.5, wm_opacity=255, wm_color="#ffffff", wm_image_path=None, output_format='webp'):
    with Image.open(input_source) as img:
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            img = img.convert('RGBA')
        else:
            img = img.convert('RGB')
            
        if target_ratio:
            img = crop_to_aspect_ratio(img, target_ratio)
            
        orig_w, orig_h = img.size
        if orig_w > max_width:
            new_h = int((max_width / orig_w) * orig_h)
            img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
            
        if watermark_text or (wm_image_path and Path(wm_image_path).exists()):
            # Create an overlay layer for the watermark to handle transparency
            wm_overlay = Image.new('RGBA', img.size, (255, 255, 255, 0))
            m = 20  # margin
            iw, ih = img.size

            def get_pos(tw, th):
                positions = {
                    'top-left': (m, m),
                    'top-right': (iw - tw - m, m),
                    'center': ((iw - tw) // 2, (ih - th) // 2),
                    'bottom-left': (m, ih - th - m),
                    'bottom-right': (iw - tw - m, ih - th - m)
                }
                return positions.get(wm_pos, positions['bottom-right'])

            # Handle Image Watermark
            if wm_image_path and Path(wm_image_path).exists():
                with Image.open(wm_image_path) as wm_img:
                    wm_img = wm_img.convert('RGBA')
                    # Scale height relative to image height
                    target_h = max(10, int(ih * (wm_size / 100)))
                    target_w = int(wm_img.size[0] * (target_h / wm_img.size[1]))
                    wm_img = wm_img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                    
                    if wm_opacity < 255:
                        mask = wm_img.getchannel('A').point(lambda p: int(p * (wm_opacity / 255)))
                        wm_img.putalpha(mask)
                    wm_overlay.paste(wm_img, get_pos(target_w, target_h), wm_img)

            # Handle Text Watermark
            if watermark_text:
                draw = ImageDraw.Draw(wm_overlay)
                try:
                    font_size = max(15, int(ih * (wm_size / 100)))
                    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", font_size)
                except:
                    font = ImageFont.load_default()
                
                bbox = draw.textbbox((0, 0), watermark_text, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                
                # Parse hex color
                color_hex = wm_color.lstrip('#')
                rgb = tuple(int(color_hex[i:i+2], 16) for i in (0, 2, 4))
                
                draw.text(get_pos(tw, th), watermark_text, fill=(*rgb, wm_opacity), font=font)
            
            img = Image.alpha_composite(img.convert('RGBA'), wm_overlay)

        # JPEG does not support transparency/alpha channel
        save_img = img.convert('RGB') if output_format.lower() == 'jpeg' else img
        # PNG ignores quality; others use it
        save_img.save(output_path, output_format.upper(), quality=quality)

# --- Web Interface Logic ---

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>The Image Forge</title>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;700&family=Crimson+Text:ital,wght@0,400;0,700;1,400&family=Space+Mono&display=swap" rel="stylesheet">
  <style>
    body { 
      font-family: 'Crimson Text', serif; background-color: #0f0d0b; color: #d1c7b7; margin: 0; padding: 3rem 1rem; 
      /* Layering the image behind a subtle gradient for readability */
      background-image: radial-gradient(circle at center, rgba(15, 13, 11, 0.4) 0%, rgba(8, 7, 6, 0.9) 100%), url('/forge_bg.webp');
      background-size: cover;
      background-attachment: fixed;
      background-position: center;
      min-height: 100vh; overflow-x: hidden; 
    }
    .container { max-width: 500px; margin: 0 auto; background: rgba(22, 19, 16, 0.92); padding: 2.5rem; border: 1px solid #3d2b1f; box-shadow: 0 10px 40px rgba(0,0,0,0.9), inset 0 0 30px rgba(226, 88, 34, 0.05); border-radius: 4px; position: relative; z-index: 2; }
    h1 { font-family: 'Cinzel', serif; color: #d4af37; text-align: center; text-transform: uppercase; font-size: 2.2rem; letter-spacing: 0.15em; margin-top: 0; margin-bottom: 2rem; text-shadow: 0 0 20px rgba(212, 175, 55, 0.4); animation: flicker 4s infinite alternate; }
    @keyframes flicker { 0%, 100% { opacity: 1; text-shadow: 0 0 20px rgba(212, 175, 55, 0.4); } 50% { opacity: 0.85; text-shadow: 0 0 10px rgba(226, 88, 34, 0.2); } }
    label { display: block; margin-top: 1.2rem; color: #e25822; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.1em; font-family: 'Cinzel', serif; }
    input[type="text"], input[type="number"], input[type="color"] { width: 100%; padding: 0.75rem; margin-top: 0.5rem; background: #0a0908; border: 1px solid #3d2b1f; color: #eae6d9; font-family: 'Space Mono', monospace; box-sizing: border-box; outline: none; transition: all 0.3s; }
    input:focus { border-color: #e25822; box-shadow: 0 0 10px rgba(226, 88, 34, 0.2); }
    .help-text { font-size: 0.8rem; color: #7a6b5d; margin-top: 0.35rem; font-style: italic; }
    select { width: 100%; padding: 0.75rem; margin-top: 0.5rem; background: #0a0a0d; border: 1px solid rgba(212, 175, 55, 0.2); color: white; font-family: 'Space Mono', monospace; outline: none; }
    button { margin-top: 2rem; width: 100%; padding: 1rem; background: #1a1612; color: #d4af37; border: 1px solid #d4af37; font-family: 'Cinzel', serif; font-weight: bold; text-transform: uppercase; letter-spacing: 0.2em; cursor: pointer; transition: all 0.4s ease; position: relative; overflow: hidden; }
    button:hover { background: #261e18; box-shadow: 0 0 20px rgba(212, 175, 55, 0.2); border-color: #f0c442; color: #f0c442; }
    .message { margin-bottom: 2rem; padding: 1rem; background: rgba(212, 175, 55, 0.05); border: 1px solid #d4af37; text-align: center; color: #d4af37; font-size: 1rem; font-family: 'Cinzel', serif; }
    .errors { margin-bottom: 2rem; padding: 1rem; background: rgba(255, 68, 68, 0.1); border: 1px solid #ff4444; color: #ff4444; font-size: 0.9rem; }
    .info-section { margin-top: 3rem; border-top: 1px solid #3d2b1f; padding-top: 2rem; }
    details { margin-bottom: 1rem; cursor: pointer; }
    summary { color: #d4af37; font-weight: bold; margin-bottom: 0.5rem; outline: none; text-transform: uppercase; font-size: 0.8rem; letter-spacing: 0.05em; }
    .info-content { font-size: 0.9rem; color: #a19385; line-height: 1.6; padding-left: 0.5rem; margin-bottom: 1rem; }
    .info-content strong { color: #e25822; }
    .alchemy-overview { border-bottom: 1px solid #3d2b1f; margin-bottom: 2rem; padding-bottom: 1.5rem; text-align: center; }
    .alchemy-overview p { font-size: 0.95rem; color: #a19385; font-style: italic; line-height: 1.7; margin: 0; }
    .errors ul { margin: 0.5rem 0 0 0; padding-left: 1.5rem; }
    /* Magical Embers */
    .embers { position: fixed; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 1; overflow: hidden; }
    .ember { position: absolute; bottom: -10px; background: rgba(226, 88, 34, 0.4); border-radius: 50%; filter: blur(2px); animation: rise 12s infinite linear; }
    @keyframes rise { 0% { transform: translateY(0) translateX(0) scale(1); opacity: 0; } 10% { opacity: 0.8; } 100% { transform: translateY(-110vh) translateX(100px) scale(0.5); opacity: 0; } }
    /* Audio Control */
    .audio-control { position: fixed; top: 1rem; right: 1rem; z-index: 100; }
    .mute-btn { background: rgba(0,0,0,0.5); border: 1px solid #d4af37; color: #d4af37; padding: 0.5rem 1rem; font-family: 'Cinzel', serif; font-size: 0.7rem; cursor: pointer; border-radius: 20px; transition: 0.3s; }
    .mute-btn:hover { background: rgba(212, 175, 55, 0.2); }
  </style>
  <script>
    window.onload = function() {
      const embersContainer = document.querySelector('.embers');
      for (let i = 0; i < 30; i++) {
        const ember = document.createElement('div');
        ember.className = 'ember';
        ember.style.left = Math.random() * 100 + 'vw';
        ember.style.width = ember.style.height = (Math.random() * 4 + 2) + 'px';
        ember.style.animationDelay = Math.random() * 12 + 's';
        ember.style.animationDuration = (Math.random() * 5 + 10) + 's';
        embersContainer.appendChild(ember);
      }
    };

    function browse(inputId, type) {
      const endpoint = type === 'folder' ? '/browse_folder' : '/browse_file';
      fetch(endpoint)
        .then(res => res.ok ? res.json() : res.json().then(err => { throw new Error(err.error) }))
        .then(data => {
          if (data.path) document.getElementById(inputId).value = data.path;
        })
        .catch(err => {
          console.error('Browser Error:', err);
          alert("Navigation Error: " + err.message);
        });
    }

    function toggleMusic() {
      const music = document.getElementById('forgeAmbient');
      const btn = document.getElementById('musicBtn');
      if (music.paused) {
        music.play();
        btn.innerText = '🔊 Sound On';
      } else {
        music.pause();
        btn.innerText = '🔇 Sound Off';
      }
    }
  </script>
</head>
<body>
  <div class="embers"></div>
  <div class="audio-control">
    <audio id="forgeAmbient" loop>
      <source src="/tnes.mp3" type="audio/mpeg">
    </audio>
    <button id="musicBtn" class="mute-btn" onclick="toggleMusic()">🔇 Sound Off</button>
  </div>
  <div class="container">
    <h1>The Image Forge</h1>

    <div class="alchemy-overview">
      <p>
        Welcome to the Forge. We refine visual data into high-performance artifacts—<strong>WebP, JPEG, PNG, or AVIF</strong>. 
        By utilizing <strong>Numeric Sequencing</strong> for precise ordering and extracting pages from <strong>PDFs</strong>, we ensure structural harmony. 
        Through <strong>Geometric Precision</strong> in resizing and aspect ratios, and the application of your <strong>Watermark</strong> (image or text), your assets are optimized for the digital landscape. 
        Standardize your collection with the <strong>Sigil of Order</strong> (XX_Naming) and navigate your local directories with ease.
      </p>
    </div>

    {% if message %}<div class="message">{{ message }}</div>{% endif %}
    {% if errors %}
    <div class="errors">
      <strong>Errors occurred:</strong>
      <ul>
        {% for error in errors %}<li>{{ error }}</li>{% endfor %}
      </ul>
    </div>
    {% endif %}
    <form method="POST">
      <label>Input Folder</label>
      <input type="text" id="input_dir" name="input_dir" value="{{ input_dir or './Raw_Images' }}" onclick="browse('input_dir', 'folder')" readonly style="cursor: pointer;">
      
      <label>Output Folder</label>
      <input type="text" id="output_dir" name="output_dir" value="{{ output_dir or './Web_Images' }}" onclick="browse('output_dir', 'folder')" readonly style="cursor: pointer;">
      
      <label>Max Width (px)</label>
      <input type="number" name="width" value="{{ width or 1200 }}">
      
      <label>Quality (1-100)</label>
      <input type="number" name="quality" value="{{ quality or 80 }}">
      
      <label>Aspect Ratio</label>
      <input type="text" name="aspect_ratio" value="{{ aspect_ratio or '' }}" placeholder="e.g., 16:9 or 1:1">
      <div class="help-text">Leave blank to keep original aspect ratio.</div>
      
      <label>Rename Pattern (Optional)</label>
      <input type="text" name="rename_pattern" value="{{ rename_pattern or '' }}" placeholder="e.g., product_{i}">
      <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; margin-top: 0.5rem; color: #7a6b5d; font-size: 0.8rem; text-transform: none; letter-spacing: normal; font-family: 'Space Mono', monospace;">
        <input type="checkbox" name="add_prefix" style="width: auto; margin: 0;" {% if add_prefix %}checked{% endif %}>
        Add numeric prefix (e.g., 01_Name)
      </label>

      <label>Output Format</label>
      <select name="output_format">
        <option value="webp" {% if output_format == 'webp' or not output_format %}selected{% endif %}>WEBP (Recommended)</option>
        <option value="jpeg" {% if output_format == 'jpeg' %}selected{% endif %}>JPEG</option>
        <option value="png" {% if output_format == 'png' %}selected{% endif %}>PNG</option>
        <option value="avif" {% if output_format == 'avif' %}selected{% endif %}>AVIF (Requires plugin)</option>
      </select>
      <div class="help-text">Note: AVIF requires 'pip install pillow-avif-plugin'.</div>
      
      <label>Watermark Text (Optional)</label>
      <input type="text" name="watermark" value="{{ watermark or '' }}" placeholder="e.g., © My Brand">
      
      <label>Watermark Image Path (Optional)</label>
      <input type="text" id="wm_image_path" name="wm_image_path" value="{{ wm_image_path or '' }}" placeholder="Click to select image..." onclick="browse('wm_image_path', 'file')" readonly style="cursor: pointer;">
      <div class="help-text">Relative or absolute path to a PNG/JPG.</div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
        <div>
          <label>WM Position</label>
          <select name="wm_pos">
            <option value="bottom-right" {% if wm_pos == 'bottom-right' or not wm_pos %}selected{% endif %}>Bottom Right</option>
            <option value="bottom-left" {% if wm_pos == 'bottom-left' %}selected{% endif %}>Bottom Left</option>
            <option value="top-right" {% if wm_pos == 'top-right' %}selected{% endif %}>Top Right</option>
            <option value="top-left" {% if wm_pos == 'top-left' %}selected{% endif %}>Top Left</option>
            <option value="center" {% if wm_pos == 'center' %}selected{% endif %}>Center</option>
          </select>
        </div>
        <div>
          <label>WM Text Color</label>
          <input type="color" name="wm_color" value="{{ wm_color or '#ffffff' }}" style="height: 44px; padding: 2px;">
        </div>
      </div>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
        <div>
          <label>WM Size (%)</label>
          <input type="number" name="wm_size" step="0.1" value="{{ wm_size or 3.5 }}">
        </div>
        <div>
          <label>WM Opacity (0-255)</label>
          <input type="number" name="wm_opacity" value="{{ wm_opacity or 255 }}">
        </div>
      </div>

      <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer; margin-top: 1.5rem;">
        <input type="checkbox" name="delete_originals" style="width: auto; margin: 0;" {% if delete_originals %}checked{% endif %}>
        Delete original images after conversion
      </label>

      <button type="submit">Optimize Images</button>
    </form>

    <div class="info-section">
      <details>
        <summary>Full Instructions</summary>
        <div class="info-content">
          <ol>
            <li><strong>Select Folders:</strong> Click the Input/Output fields to browse your local directories.</li>
            <li><strong>Ordering:</strong> Files are sorted numerically based on numbers found in the filename (e.g., "image_2" stays before "image_10").</li>
            <li><strong>PDFs:</strong> PDFs are automatically split into individual images based on their page numbers.</li>
            <li><strong>Naming:</strong> Use the "Add numeric prefix" checkbox to prepend <code>XX_</code> to your files. If unchecked and no pattern is used, original filenames are kept completely raw and unchanged.</li>
            <li><strong>Watermarks:</strong> You can use text, an image logo, or both. The "Size" affects the watermark height relative to the image.</li>
            <li><strong>Atmospheric Specs:</strong> For custom backgrounds, use <strong>2560x1440px WebP</strong> with a 16:9 aspect ratio and dark central focus.</li>
          </ol>
        </div>
      </details>

      <details>
        <summary>FAQ</summary>
        <div class="info-content">
          <p><strong>Why isn't the file navigator opening?</strong><br>
          On macOS, ensure the Python terminal has "Accessibility" or "Full Disk Access" permissions if prompted. The window may occasionally appear behind your browser.</p>
          
          <p><strong>How do I enable AVIF?</strong><br>
          Run <code>pip install pillow-avif-plugin</code> in your terminal and restart this app.</p>
          
          <p><strong>Can I convert PDFs?</strong><br>
          Yes, but you must run <code>pip install pymupdf</code> first.</p>
          
          <p><strong>What is 'Quality'?</strong><br>
          For WebP and JPEG, lower quality (e.g., 70-80) significantly reduces file size with minimal visual loss. PNG is lossless and ignores this setting.</p>

          <p><strong>Transparency:</strong><br>
          WebP and PNG support transparency. If you convert a transparent image to JPEG, the background will turn black.</p>
        </div>
      </details>
    </div>
  </div>
</body>
</html>
"""

@app.route("/forge_bg.webp")
def serve_background():
    """Serves the forge background image from the local directory."""
    return send_from_directory(Path(__file__).resolve().parent, "forge_bg.webp")

@app.route("/tnes.mp3")
def serve_music():
    """Serves the background music file from the local directory."""
    return send_from_directory(Path(__file__).resolve().parent, "tnes.mp3")

@app.route("/browse_file")
def browse_file():
    if not tk:
        return {"error": "Tkinter (navigation library) not found. Please ensure it is installed."}, 500
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        file_path = filedialog.askopenfilename(filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff *.webp")])
        root.update()
        root.destroy()
        return {"path": file_path}
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/browse_folder")
def browse_folder():
    if not tk:
        return {"error": "Tkinter (navigation library) not found. Please ensure it is installed."}, 500
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        folder_path = filedialog.askdirectory()
        root.update()
        root.destroy()
        return {"path": folder_path}
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/", methods=["GET", "POST"])
def index():
    message = ""
    errors = []
    add_prefix = True
    if request.method == "POST":
        in_dir, out_dir = request.form.get("input_dir"), request.form.get("output_dir")
        w, q, ar = int(request.form.get("width")), int(request.form.get("quality")), request.form.get("aspect_ratio")
        rename_pattern = request.form.get("rename_pattern", "").strip()
        add_prefix = request.form.get("add_prefix") == "on"
        output_format = request.form.get("output_format", "webp").lower()
        watermark = request.form.get("watermark", "").strip()
        wm_pos = request.form.get("wm_pos", "bottom-right")
        wm_size = float(request.form.get("wm_size", 3.5))
        wm_opacity = int(request.form.get("wm_opacity", 255))
        wm_color = request.form.get("wm_color", "#ffffff")
        wm_image_path = request.form.get("wm_image_path", "").strip()
        delete_originals = request.form.get("delete_originals") == "on"
        
        in_path, out_path = Path(in_dir), Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        t_ratio = parse_aspect_ratio(ar.strip()) if ar.strip() else None
        
        if output_format == 'avif' and not pillow_avif:
            errors.append("AVIF support requires the 'pillow-avif-plugin'. Run 'pip install pillow-avif-plugin' and restart the script.")
        elif in_path.exists() and in_path.is_dir():
            # Filter and sort files based on numeric content in filenames
            extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.pdf', '.webp'}
            sorted_files = sorted(
                [f for f in in_path.iterdir() if f.is_file() and f.suffix.lower() in extensions],
                key=get_sort_key
            )
            
            count = 0
            for f in sorted_files:
                ext = f.suffix.lower()
                try:
                    if ext == '.pdf':
                        if not fitz:
                            errors.append(f"{f.name}: PyMuPDF (fitz) not installed.")
                            continue
                        
                        doc = fitz.open(f)
                        for page_index in range(len(doc)):
                            pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(300/72, 300/72))
                            image_bytes = pix.tobytes("png")
                            
                            new_stem = get_new_stem(rename_pattern, f.stem, count, add_prefix, is_pdf=True)
                            process_image(io.BytesIO(image_bytes), out_path / f"{new_stem}.{output_format}", w, q, t_ratio, watermark, wm_pos, wm_size, wm_opacity, wm_color, wm_image_path, output_format)
                            count += 1
                        doc.close()
                    else:
                        new_stem = get_new_stem(rename_pattern, f.stem, count, add_prefix)
                        process_image(f, out_path / f"{new_stem}.{output_format}", w, q, t_ratio, watermark, wm_pos, wm_size, wm_opacity, wm_color, wm_image_path, output_format)
                        count += 1

                    if delete_originals:
                        f.unlink()
                except Exception as e:
                    errors.append(f"{f.name}: {str(e)}")

            message = f"Success! {count} images optimized to {out_dir}."
        else:
            message = f"Error: '{in_dir}' does not exist."
            
    return render_template_string(HTML_TEMPLATE, message=message, errors=errors, add_prefix=add_prefix, **request.form)

def get_new_stem(pattern, original_stem, current_count, add_prefix, is_pdf=False):
    if not pattern and not add_prefix:
        if is_pdf:
            return f"{original_stem}_{current_count + 1}"
        return original_stem
        
    source_name = pattern if pattern else original_stem
    
    if add_prefix:
        clean_name = source_name.replace("{i}", "").strip("_").replace(" ", "_")
        index_str = f"{current_count + 1:02d}"
        return f"{index_str}_{clean_name}"
    else:
        if "{i}" in source_name:
            return source_name.replace("{i}", str(current_count + 1)).replace(" ", "_")
        if is_pdf:
            return f"{source_name.replace(' ', '_')}_{current_count + 1}"
        return source_name.replace(" ", "_")

if __name__ == "__main__":
    port = 5005
    print(f"\n[SYSTEM] Starting Optimizer...")
    print(f"[SYSTEM] Local URL: http://127.0.0.1:{port}")
    print("[SYSTEM] If you see 'Address already in use' in this terminal, change the port variable.")
    print("[SYSTEM] Press CTRL+C to stop the server.\n")
    
    # 0.0.0.0 is more robust for local connections on modern macOS
    threading.Timer(2.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host='0.0.0.0', port=port, debug=False, threaded=False)