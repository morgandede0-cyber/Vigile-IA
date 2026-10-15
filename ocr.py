"""Extraction prudente du pseudo JAUNE sous l'en-tête Profil.
Le texte de l'interface (notamment « Profil ») n'est jamais accepté comme pseudo.
"""
from __future__ import annotations
from dataclasses import dataclass
from io import BytesIO
from collections import defaultdict
import re
from PIL import Image, ImageOps, ImageEnhance, ImageFilter
import pytesseract
from pytesseract import Output

@dataclass(frozen=True)
class OCRResult:
    name: str | None
    confidence: float
    reason: str

UI_WORDS = {'profil', 'profile', 'saison', 'conseillers', 'informations joueur', 'joueur', 'skins de ville', 'aperçu des statistiques', 'niveau', 'clan'}

def clean_name(raw: str) -> str:
    raw = re.sub(r'\s+', ' ', raw).strip(' _-.,:;|!\t\n')
    if not 2 <= len(raw) <= 32 or raw.casefold() in UI_WORDS:
        return ''
    if not re.fullmatch(r"[\wÀ-ÿ'’ .-]+", raw, flags=re.UNICODE):
        return ''
    return raw if any(ch.isalpha() for ch in raw) else ''

def extract_name(image_bytes: bytes, min_confidence: float = 70) -> OCRResult:
    try:
        im = Image.open(BytesIO(image_bytes))
        if im.width < 400 or im.height < 230 or im.width * im.height > 20_000_000:
            return OCRResult(None, 0, 'Résolution ou format non conforme')
        im = im.convert('RGB')
        w, h = im.size
        # L'interface du jeu occupe généralement toute la capture 16:9.
        # La zone du pseudo est SOUS « Profil », vers 31–43 % de la hauteur.
        # Une bande un peu plus large accepte de légères variations d'échelle.
        regions = [(.38,.30,.64,.43), (.40,.32,.62,.40)]
        candidates = defaultdict(list)
        for x1,y1,x2,y2 in regions:
            crop = im.crop((int(w*x1),int(h*y1),int(w*x2),int(h*y2)))
            hsv = crop.convert('HSV')
            # Garder seulement les pixels jaunes/orangés saturés du pseudo.
            # Le titre « Profil » est blanc et est donc exclu.
            mask = Image.new('L', crop.size, 0)
            src = hsv.load(); dst = mask.load()
            yellow_count = 0
            for y in range(crop.height):
                for x in range(crop.width):
                    hue,sat,val = src[x,y]
                    if 15 <= hue <= 55 and sat >= 85 and val >= 90:
                        dst[x,y] = 255
                        yellow_count += 1
            if yellow_count < 8:
                continue
            # Isoler les lignes contenant le texte jaune, sans inclure les autres labels.
            line_counts = [sum(mask.crop((0,y,crop.width,y+1)).getdata())//255 for y in range(crop.height)]
            spans=[]; active=None
            for y,count in enumerate(line_counts+[0]):
                if count >= 2 and active is None: active=y
                elif count < 2 and active is not None:
                    if y-active >= 3: spans.append((active,y))
                    active=None
            for top,bottom in spans:
                if bottom-top > crop.height*.6: continue
                band = mask.crop((0,max(0,top-3),crop.width,min(crop.height,bottom+3)))
                bbox = band.getbbox()
                if not bbox: continue
                band=band.crop((max(0,bbox[0]-5),0,min(band.width,bbox[2]+5),band.height))
                if band.width < 12: continue
                large=band.resize((band.width*5,band.height*5),Image.Resampling.NEAREST)
                for variant in (large, ImageOps.invert(large)):
                    for psm in (7,13):
                        data=pytesseract.image_to_data(variant,lang='eng',config=f'--psm {psm}',output_type=Output.DICT)
                        words=[]; scores=[]
                        for word,score in zip(data['text'],data['conf']):
                            try: score=float(score)
                            except (TypeError,ValueError): continue
                            if word.strip() and score>=0:
                                words.append(word.strip());scores.append(score)
                        name=clean_name(' '.join(words))
                        if name and scores:
                            candidates[name.casefold()].append((name,sum(scores)/len(scores)))
        if not candidates:
            return OCRResult(None,0,'Pseudo jaune introuvable ou illisible')
        best=max(candidates.values(),key=lambda group:(len(group),max(x[1] for x in group)))
        name,score=max(best,key=lambda pair:pair[1])
        score=round(score,1)
        if len(best)<2 or score<min_confidence:
            return OCRResult(name,score,'Reconnaissance incertaine')
        return OCRResult(name,score,'OK')
    except (OSError,ValueError,pytesseract.TesseractError) as exc:
        return OCRResult(None,0,f'Erreur OCR : {type(exc).__name__}')
