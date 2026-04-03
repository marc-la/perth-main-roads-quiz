#!/usr/bin/env python3
"""
Post-process a mapshaper-exported SVG for JetPunk quiz compatibility.

Usage:
    python postprocess_svg.py <input_svg> <suburbs_csv> <roads_tsv> <quiz_tsv> <output_svg>

Example:
    python scripts/postprocess_svg.py \
        build/jetpunk_mapshaper.svg \
        build/suburbs_names.csv \
        build/roads_final.tsv \
        quiz/roads_quiz.tsv \
        quiz/jetpunk_final.svg

What it does:
  1. Injects <title> elements into suburb paths for hover tooltips
  2. Adds class attributes to road paths (primary/trunk/motorway) from roads_final.tsv
  3. Adds a <style> block with road class styling + JetPunk overrides
  4. Strips inline style attributes from road paths (so CSS classes work)
  5. Reports any TSV road IDs not found in the SVG (cropped out roads)
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)


def compress_path_d(d, precision=1):
    """Reduce coordinate precision and use relative/compact SVG path notation."""
    # Round all numbers to given decimal precision
    def round_num(match):
        val = float(match.group())
        rounded = round(val, precision)
        # Use integer if no fractional part
        if rounded == int(rounded):
            return str(int(rounded))
        return f"{rounded:.{precision}f}"

    return re.sub(r"-?\d+\.\d+", round_num, d)

STYLE_BLOCK = """\
/* Suburb base styling - muted so roads stand out */
#suburbs path {
    fill: #f0eeec;
    stroke: #ddd8d4;
    stroke-width: 0.3;
    opacity: 0.6;
}

/* Road styling by classification - bold and prominent */
#roads path { fill: none; }
.primary  { stroke: #707070; stroke-width: 1.2; opacity: 1; }
.trunk    { stroke: #505050; stroke-width: 1.8; opacity: 1; }
.motorway { stroke: #303030; stroke-width: 2.4; opacity: 1; }

/*
 * JetPunk .svg-correct / .svg-incorrect overrides.
 * Roads use stroke not fill, so we override JetPunk's default
 * fill-based coloring to use stroke instead.
 */
.svg-correct {
    fill: none !important;
    stroke: #66FF66 !important;
    stroke-width: 3px !important;
    opacity: 1 !important;
}
.svg-incorrect {
    fill: none !important;
    stroke: #FF6666 !important;
    stroke-width: 3px !important;
    opacity: 1 !important;
}
"""


def load_suburb_names(csv_path):
    """Load LOC_PID -> LOC_NAME mapping from CSV."""
    names = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row["LOC_PID"].strip()
            name = row["LOC_NAME"].strip()
            names[pid] = name
    return names


def load_road_classes(tsv_path):
    """Load quiz_id -> highway class mapping from roads_final.tsv."""
    classes = {}
    with open(tsv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            quiz_id = row["quiz_id"].strip()
            highway = row["highway"].strip()
            classes[quiz_id] = highway
    return classes


def load_quiz_ids(tsv_path):
    """Load the set of road IDs from the quiz TSV."""
    ids = set()
    with open(tsv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)
        for row in reader:
            if row:
                ids.add(row[0].strip())
    return ids


def process_svg(input_path, suburb_names, road_classes, quiz_ids, output_path):
    tree = ET.parse(input_path)
    root = tree.getroot()

    # Ensure width/height are present and in pixels
    for attr in ("width", "height"):
        val = root.get(attr, "")
        # Strip any unit suffix, keep number
        num = re.match(r"[\d.]+", val)
        if num:
            root.set(attr, num.group())

    # Remove baseProfile (unnecessary for JetPunk)
    if "baseProfile" in root.attrib:
        del root.attrib["baseProfile"]

    # Insert <style> as first child
    style_el = ET.SubElement(root, f"{{{NS}}}style")
    style_el.text = STYLE_BLOCK
    # Move style to be the first child
    root.remove(style_el)
    root.insert(0, style_el)

    svg_road_ids = set()

    for group in root:
        group_id = group.get("id", "")

        if group_id == "suburbs":
            for path in group:
                if path.tag != f"{{{NS}}}path":
                    continue
                path_id = path.get("id", "")
                # Compress path coordinates
                if "d" in path.attrib:
                    path.set("d", compress_path_d(path.get("d")))
                # Extract LOC_PID from suburb ID (format: "suburb-LOC_PID" or just LOC_PID)
                loc_pid = path_id.replace("suburb-", "") if path_id.startswith("suburb-") else path_id
                suburb_name = suburb_names.get(loc_pid)
                if not suburb_name:
                    # Try matching without prefix
                    for pid, name in suburb_names.items():
                        if pid in path_id:
                            suburb_name = name
                            break
                if suburb_name:
                    title_el = ET.SubElement(path, f"{{{NS}}}title")
                    title_el.text = suburb_name
                # Strip inline styles that will be handled by CSS
                for attr in ("fill", "stroke", "stroke-width", "opacity"):
                    if attr in path.attrib:
                        del path.attrib[attr]

        elif group_id == "roads":
            for path in group:
                if path.tag != f"{{{NS}}}path":
                    continue
                path_id = path.get("id", "")
                if path_id:
                    svg_road_ids.add(path_id)
                # Compress path coordinates
                if "d" in path.attrib:
                    path.set("d", compress_path_d(path.get("d")))
                # Add class from highway type (primary/trunk/motorway)
                highway_class = road_classes.get(path_id)
                if highway_class:
                    path.set("class", highway_class)
                # Strip inline styles — CSS classes handle these
                for attr in ("stroke", "stroke-width", "opacity", "fill"):
                    if attr in path.attrib:
                        del path.attrib[attr]
            # Remove group-level inline styles too (CSS handles it)
            for attr in ("fill", "stroke", "stroke-width"):
                if attr in group.attrib:
                    del group.attrib[attr]
            # Ensure roads group has fill=none (roads are lines, not shapes)
            group.set("fill", "none")

    # Report mismatches
    missing_in_svg = quiz_ids - svg_road_ids
    missing_in_tsv = svg_road_ids - quiz_ids

    if missing_in_svg:
        print(f"\n[WARNING] {len(missing_in_svg)} TSV road IDs not found in SVG (cropped out?):")
        for rid in sorted(missing_in_svg):
            print(f"  - {rid}")
        print("  -> Remove these from quiz/roads_quiz.tsv before uploading.\n")

    if missing_in_tsv:
        print(f"\n[INFO] {len(missing_in_tsv)} SVG road IDs not in TSV (won't be quiz answers):")
        for rid in sorted(missing_in_tsv):
            print(f"  - {rid}")

    matched = quiz_ids & svg_road_ids
    print(f"\n[OK] {len(matched)} roads matched between TSV and SVG.")

    # Write output
    tree.write(output_path, xml_declaration=True, encoding="unicode")
    print(f"[OK] Written to {output_path}")


def main():
    if len(sys.argv) != 6:
        print(__doc__)
        sys.exit(1)

    input_svg, suburbs_csv, roads_tsv, quiz_tsv, output_svg = sys.argv[1:6]

    suburb_names = load_suburb_names(suburbs_csv)
    print(f"Loaded {len(suburb_names)} suburb names from {suburbs_csv}")

    road_classes = load_road_classes(roads_tsv)
    print(f"Loaded {len(road_classes)} road class mappings from {roads_tsv}")

    quiz_ids = load_quiz_ids(quiz_tsv)
    print(f"Loaded {len(quiz_ids)} quiz road IDs from {quiz_tsv}")

    process_svg(input_svg, suburb_names, road_classes, quiz_ids, output_svg)


if __name__ == "__main__":
    main()
