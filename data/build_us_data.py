"""Rebuild data/us_states.csv, us_cities.csv and us_zips.csv from the GeoNames exports (CC BY 4.0).

Download https://download.geonames.org/export/dump/cities1000.zip and https://download.geonames.org/export/zip/US.zip
into an empty folder, then run:  python data/build_us_data.py <that folder>
"""
import csv
import io
import re
import sys
import zipfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(__file__).parent
SKIP_FEATURES = {"PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH"}  # city sections, historical, abandoned, destroyed

zips, states = [], {}
with zipfile.ZipFile(src / "US.zip") as z:
    for f in csv.reader(io.TextIOWrapper(z.open("US.txt"), encoding="utf-8"), delimiter="\t"):
        zipcode, place, state_name, state = f[1], f[2], f[3], f[4]
        if len(state) == 2 and state_name:  # skips military APO/FPO codes, which have no state name
            states[state] = state_name
            zips.append((zipcode, place, state, f[9], f[10]))

cities = {}
with zipfile.ZipFile(src / "cities1000.zip") as z:
    for f in csv.reader(io.TextIOWrapper(z.open("cities1000.txt"), encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
        if f[8] != "US" or f[6] != "P" or f[7] in SKIP_FEATURES or f[10] not in states:
            continue
        name = re.sub(r"\s*\(.*?\)", "", f[1]).strip()  # "Middlebury (village)" -> "Middlebury"
        key, pop = (name, f[10]), int(f[14] or 0)
        if pop > cities.get(key, (0,))[0]:  # same name twice in one state: keep the bigger place
            cities[key] = (pop, f[4], f[5])


def write(name, header, rows):
    with open(out / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    print(f"{name}: {len(rows)} rows")


write("us_states.csv", ["code", "name"], sorted(states.items()))
write("us_cities.csv", ["city", "state", "population", "lat", "lon"],
      sorted(((c, s, p, la, lo) for (c, s), (p, la, lo) in cities.items()), key=lambda r: (r[1], -r[2])))
write("us_zips.csv", ["zip", "city", "state", "lat", "lon"], sorted(zips))
