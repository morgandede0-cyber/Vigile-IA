"""OCR ciblé sur la zone noire du pseudo dans la section Profil.
Capture de référence 720x416, pseudo dans x≈290..445, y≈110..137.
La détection est prudente : un doute passe en revue humaine.
"""
from __future__ import annotations
from dataclasses import dataclass
from io import BytesIO
import re
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract
from pytesseract import Output

@dataclass(frozen=True)
class OCRResult:
    name: str | None
    confidence: float
    reason: str


def clean_name(raw: str) -> str:
    raw = re.sub(r"\s+", " ", raw).strip(" _-.,:;|!\t\n")
    if not 2 <= len(raw) <= 32:
        return ""
    if not re.fullmatch(r"[\wÀ-ÿ'’ .-]+", raw, flags=re.UNICODE):
        return ""
    if not any(ch.isalpha() for ch in raw):
        return ""
    return raw


def extract_name(image_bytes: bytes, min_confidence: float = 70) -> OCRResult:
    try:
        im = Image.open(BytesIO(image_bytes))
        if im.width < 400 or im.height < 230 or im.width / im.height < 1.3 or im.width / im.height > 2.4:
            return OCRResult(None, 0, "Format ou résolution de capture non conforme")
        if im.width * im.height > 20_000_000:
            return OCRResult(None, 0, "Image trop grande")
        im = im.convert("RGB")
        w, h = im.size
        # Profil fixe de l'interface de référence; une interface différente passe au staff.
        box = (int(w * .403), int(h * .269), int(w * .617), int(h * .327))
        crop = im.crop(box)
        variants = []
        enlarged = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.LANCZOS)
        variants.append(enlarged)
        variants.append(ImageEnhance.Contrast(enlarged.convert("L")).enhance(2.5))
        variants.append(ImageOps.autocontrast(enlarged.convert("L")).filter(ImageFilter.SHARPEN))
        candidates = []
        for variant in variants:
            data = pytesseract.image_to_data(variant, lang="eng", config="--psm 6", output_type=Output.DICT)
            words, scores = [], []
            for word, score in zip(data["text"], data["conf"]):
                try:
                    value = float(score)
                except (TypeError, ValueError):
                    continue
                if word.strip() and value >= 0:
                    words.append(word.strip())
                    scores.append(value)
            name = clean_name(" ".join(words))
            if name and scores:
                candidates.append((name, sum(scores) / len(scores)))
        if not candidates:
            return OCRResult(None, 0, "Pseudo introuvable ou illisible")
        # Exiger deux lectures concordantes, sinon revue humaine.
        groups = {}
        for name, score in candidates:
            groups.setdefault(name.casefold(), []).append((name, score))
        best = max(groups.values(), key=lambda group: (len(group), max(s for _, s in group)))
        name, confidence = max(best, key=lambda pair: pair[1])
        if len(best) < 2 or confidence < min_confidence:
            return OCRResult(name, round(confidence, 1), "Reconnaissance incertaine")
        return OCRResult(name, round(confidence, 1), "OK")
    except (OSError, ValueError, pytesseract.TesseractError) as exc:
        return OCRResult(None, 0, f"Erreur de lecture OCR : {type(exc).__name__}")
