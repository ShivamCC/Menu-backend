# main.py
from fastapi import FastAPI, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional
import requests
import pandas as pd
from datetime import datetime
import os

app = FastAPI()

# Allow frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://menufrontend-six.vercel.app"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

@app.get("/health")
def health_check():
    return {"status": "ok"}


# ---------- Utility functions ----------

def safe_get_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}

def parse_item(info, restaurant_name, restaurant_id, category_title, subzone_name):
    """Parse a single menu item"""
    if not info:
        return []
    
    imageId = info.get("imageId")
    imageUrl = f"https://media-assets.swiggy.com/swiggy/image/upload/{imageId}" if imageId else None

    base_price = round((info.get("price") or info.get("defaultPrice") or 0)/100, 2)
    final_price = round(info.get("finalPrice", 0)/100, 2) if info.get("finalPrice") else None
    flashSale = "ON" if final_price and final_price < base_price else "OFF"
    dish_instock = info.get("inStock", 0)
    
    variants = []
    for vg in info.get("variantsV2", {}).get("variantGroups", []):
        group_name = vg.get("name")
        for var in vg.get("variations", []):
            raw_price = var.get("price") or var.get("defaultPrice") or 0
            try:
                raw_price = int(raw_price)
            except Exception:
                raw_price = 0
            variants.append({
                "variant_group": group_name,
                "variant_name": var.get("name"),
                "variant_price_add": round(raw_price/1, 2),
                "variant_inStock": var.get("inStock", None),
                "variant_isDefault": var.get("default", 0),
                "variant_isEnabled": var.get("isEnabled", True),
            })

    return [{
        "res_id": restaurant_id,
        "restaurant": restaurant_name,
        "subzone": subzone_name or "",
        "category": category_title or info.get("category", ""),
        "dish_name": info.get("name", ""),
        "description": info.get("description", "") or "",
        "base_price": base_price,
        "final_price": final_price,
        "flashSale": flashSale,
        "inStock": bool(dish_instock),
        "image": imageUrl,
        "variants": variants,
    }]

def parse_menu_from_data(data):
    """Parse all menu items from Swiggy API data"""
    items = []
    restaurant_name = None
    restaurant_id = None
    subzone_name = None
    try:
        cards = data.get("data", {}).get("cards", [])
        # Extract restaurant info
        for c in cards:
            try:
                info = c.get("card", {}).get("card", {}).get("info")
                if info and info.get("name"):
                    restaurant_name = info.get("name")
                    restaurant_id = info.get("id")
                    subzone_name = info.get("areaName") or info.get("subzoneName") or ""
                    break
            except:
                continue

        # Extract grouped cards
        grouped_cards = []
        for c in cards:
            try:
                grouped = c.get("groupedCard", {}).get("cardGroupMap", {}).get("REGULAR", {}).get("cards", [])
                if grouped:
                    grouped_cards = grouped
                    break
            except:
                continue

        for card in grouped_cards:
            card_obj = card.get("card", {}).get("card", {})
            categories = card_obj.get("categories")
            if categories:
                for cat in categories:
                    cat_title = cat.get("title") or ""
                    item_cards = cat.get("itemCards", [])
                    for itemCard in item_cards:
                        info = itemCard.get("card", {}).get("info", {})
                        items.extend(parse_item(info, restaurant_name, restaurant_id, cat_title, subzone_name))
            else:
                item_cards = card_obj.get("itemCards", [])
                for itemCard in item_cards:
                    info = itemCard.get("card", {}).get("info") or itemCard.get("card", {}).get("card", {}).get("info") or {}
                    cat_title = card_obj.get("title", "") or ""
                    items.extend(parse_item(info, restaurant_name, restaurant_id, cat_title, subzone_name))

    except Exception:
        pass
    return items

def extract_offers_from_data(data):
    """Extract offers safely"""
    offers_list = []
    subzone_name = None
    restaurant_name = None
    try:
        cards = data.get("data", {}).get("cards", [])
        for c in cards:
            try:
                info = c.get("card", {}).get("card", {}).get("info")
                if info and info.get("name"):
                    restaurant_name = info.get("name")
                    subzone_name = info.get("areaName") or info.get("subzoneName") or ""
                    break
            except:
                continue

        for card in cards:
            try:
                card_data = card.get("card", {}).get("card", {})
                offers_candidates = []

                if "gridElements" in card_data:
                    offers_candidates.extend(card_data["gridElements"].get("infoWithStyle", {}).get("offers", []))
                if "offers" in card_data:
                    offers_candidates.extend(card_data.get("offers", []))
                if "cards" in card_data:
                    for nested in card_data["cards"]:
                        nested_card = nested.get("card", {}).get("card", {})
                        if "offers" in nested_card:
                            offers_candidates.extend(nested_card.get("offers", []))

                for o in offers_candidates:
                    info = o.get("info", {})
                    offers_list.append({
                        "res_id": restaurant_name,
                        "restaurant": restaurant_name or "",
                        "subzone": subzone_name or "",
                        "title": info.get("header") or "Offer",
                        "code": info.get("couponCode") or "",
                        "description": info.get("description") or "",
                        "discount": info.get("discountType") or "",
                        "image": info.get("offerLogo") or "https://via.placeholder.com/120?text=No+Logo"
                    })
            except Exception:
                continue
    except Exception:
        pass
    return offers_list

def fetch_menu_for_resid(res_id: str):
    url = f"https://www.swiggy.com/mapi/menu/pl?page-type=REGULAR_MENU&complete-menu=true&lat=19.0748&lng=72.8856&restaurantId={res_id}"
    r = requests.get(url, headers=HEADERS, timeout=15)
    data = safe_get_json(r)
    items = parse_menu_from_data(data)
    offers = extract_offers_from_data(data)
    print(f"Fetched {len(items)} items and {len(offers)} offers for {res_id}")
    return items, offers

# ---------- Flatten Variants for Excel ----------
def flatten_items_with_variants(items):
    """Flatten menu items so each variant is a separate row"""
    flat_rows = []
    for item in items:
        variants = item.pop("variants", [])
        if variants:
            for var in variants:
                row = item.copy()
                row.update({
                    "variant_group": var.get("variant_group"),
                    "variant_name": var.get("variant_name"),
                    "variant_price_add": var.get("variant_price_add"),
                    "variant_inStock": var.get("variant_inStock"),
                    "variant_isDefault": var.get("variant_isDefault"),
                })
                flat_rows.append(row)
        else:
            # No variants, keep item as is but add empty variant fields
            row = item.copy()
            row.update({
                "variant_group": None,
                "variant_name": None,
                "variant_price_add": None,
                "variant_inStock": None,
                "variant_isDefault": None,
            })
            flat_rows.append(row)
    return flat_rows

# ---------- Existing API Endpoints ----------

@app.get("/swiggy/preview")
def preview(res_id: str = Query(..., description="Comma-separated restaurant IDs")):
    ids = [x.strip() for x in res_id.split(",") if x.strip()]
    all_items = []
    all_offers = []
    for rid in ids:
        try:
            items, offers = fetch_menu_for_resid(rid)
            all_items.extend(items)
            all_offers.extend(offers)
        except Exception as e:
            print(f"Error fetching {rid}: {e}")
            continue
    return JSONResponse(content={"items": all_items, "offers": all_offers})

def scrape_and_generate_excel(res_ids_dict):
    restaurant_names = []
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    temp_filename = "temp.xlsx"
    with pd.ExcelWriter(temp_filename) as writer:
        for client, resids in res_ids_dict.items():
            all_items = []
            all_offers = []
            for res_id in resids:
                try:
                    items, offers = fetch_menu_for_resid(res_id)
                    # Flatten variants
                    items_flat = flatten_items_with_variants(items)
                    all_items.extend(items_flat)
                    all_offers.extend(offers)
                    for i in items:
                        if i["restaurant"] not in restaurant_names:
                            restaurant_names.append(i["restaurant"])
                except Exception as e:
                    print(f"Error fetching {res_id}: {e}")
                    continue

            if all_items:
                pd.DataFrame(all_items).to_excel(writer, sheet_name=f"{client}_Menu", index=False)
            if all_offers:
                pd.DataFrame(all_offers).to_excel(writer, sheet_name=f"{client}_Offers", index=False)

    name_part = "_".join([name.replace(" ", "_") for name in restaurant_names])
    if len(name_part) > 50:
        name_part = name_part[:50]
    final_filename = f"{name_part}_{timestamp}.xlsx"
    try:
        os.replace(temp_filename, final_filename)
    except Exception:
        final_filename = temp_filename
    return final_filename

@app.get("/swiggy/download")
def download_excel(res_id: Optional[str] = Query(..., description="Comma-separated list of restaurant IDs")):
    res_ids_dict = {"Client": res_id.split(",")}
    file_path = scrape_and_generate_excel(res_ids_dict)
    return FileResponse(
        file_path,
        filename=file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ---------- NEW Endpoint: Compare Offers ----------
@app.post("/swiggy/compare_offers")
async def compare_offers(
    file: UploadFile = File(...),
    res_ids: str = Query(..., description="Comma-separated restaurant IDs")
):
    """
    Compare scraped offers with reference sheet.
    Returns mismatched offers including subzone.
    """
    # Step 1: Read reference file
    try:
        df_ref = pd.read_excel(file.file)
    except Exception:
        try:
            df_ref = pd.read_csv(file.file)
        except Exception:
            return JSONResponse(content={"error": "Invalid file format"}, status_code=400)

    # Normalize reference offers for comparison
    df_ref['title'] = df_ref['title'].astype(str).str.strip()
    df_ref['code'] = df_ref['code'].astype(str).str.strip()
    
    # Step 2: Scrape offers for all res_ids
    ids = [x.strip() for x in res_ids.split(",") if x.strip()]
    scraped_offers = []
    for rid in ids:
        try:
            _, offers = fetch_menu_for_resid(rid)
            scraped_offers.extend(offers)
        except Exception as e:
            print(f"Error fetching {rid}: {e}")
            continue

    df_scraped = pd.DataFrame(scraped_offers)
    if df_scraped.empty:
        return JSONResponse(content={"error": "No offers scraped"}, status_code=404)
    
    # Normalize scraped offers
    df_scraped['title'] = df_scraped['title'].astype(str).str.strip()
    df_scraped['code'] = df_scraped['code'].astype(str).str.strip()
    
    # Step 3: Find mismatches
    df_scraped['match'] = df_scraped.apply(
        lambda row: ((df_ref['title'] == row['title']) & (df_ref['code'] == row['code'])).any(), axis=1
    )
    
    mismatches = df_scraped[~df_scraped['match']].drop(columns=['match']).to_dict(orient='records')
    
    return JSONResponse(content={"mismatches": mismatches})
