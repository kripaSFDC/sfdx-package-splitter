"""
Generate a package.xml of any size, from nothing.

    python make-manifest.py 40000 > package.xml          # ~40,000 lines
    python make-manifest.py 40000 --shape from-org.xml   # borrow a real org's proportions

Why this exists: the manifest that caused the problem belonged to a client and
cannot be published. The *shape* of a big enterprise manifest is not
confidential - it is just Salesforce metadata type names and a lot of members.
This reproduces the shape at any size, so the failure is reproducible by anyone
watching, which the original file never would have been.

Nothing here came from any client org. Names are generated.
"""
import sys, random, xml.etree.ElementTree as ET
from collections import Counter

# cmd.exe is not UTF-8. Replace unmappable characters instead of crashing.
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# Proportions of a large, long-lived enterprise org, by component count.
# Fields dominate; everyone underestimates by how much.
SHAPE = [
    ("CustomField",             0.380),
    ("ApexClass",               0.095),
    ("Layout",                  0.080),
    ("Report",                  0.070),
    ("Flow",                    0.055),
    ("CustomLabel",             0.040),
    ("ListView",                0.038),
    ("LightningComponentBundle",0.030),
    ("QuickAction",             0.028),
    ("RecordType",              0.026),
    ("EmailTemplate",           0.025),
    ("ValidationRule",          0.024),
    ("StaticResource",          0.020),
    ("AuraDefinitionBundle",    0.018),
    ("CustomObject",            0.016),
    ("FlexiPage",               0.015),
    ("Dashboard",               0.012),
    ("PermissionSet",           0.010),
    ("WebLink",                 0.008),
    ("CustomTab",               0.003),
    ("ApexTrigger",             0.003),
    ("CustomMetadata",          0.002),
    ("CustomApplication",       0.001),
    ("Profile",                 0.001),
]

DOMAINS = ["Order","Account","Contract","Asset","Billing","Shipment","Catalogue",
           "Partner","Claim","Policy","Ticket","Invoice","Quote","Territory",
           "Campaign","Entitlement","Warranty","Rebate","Pricing","Fulfilment"]
QUALS   = ["Sync","Legacy","V2","Batch","Async","External","Staging","Audit",
           "Compliance","Regional","Bulk","Inbound","Outbound","Archive"]

def member(t, rnd, i):
    d, q = rnd.choice(DOMAINS), rnd.choice(QUALS)
    if t == "CustomField":   return f"{d}_{q}__c.{rnd.choice(DOMAINS)}_{i}__c"
    if t == "ValidationRule":return f"{d}_{q}__c.Require_{rnd.choice(DOMAINS)}_{i}"
    if t == "RecordType":    return f"{d}_{q}__c.{rnd.choice(DOMAINS)}_{i}"
    if t == "ListView":      return f"{d}_{q}__c.{rnd.choice(DOMAINS)}_{i}"
    if t == "WebLink":       return f"{d}_{q}__c.Open_{rnd.choice(DOMAINS)}_{i}"
    if t == "Layout":        return f"{d}_{q}__c-{d} {q} Layout {i}"
    if t == "CustomObject":  return f"{d}_{q}_{i}__c"
    if t == "Profile":       return f"{d} {q} Profile {i}"
    if t == "CustomLabel":   return f"{d}_{q}_Label_{i}"
    return f"{d}{q}{t.replace('Definition','').replace('Bundle','')}{i}"

def shape_from(path):
    root = ET.parse(path).getroot()
    ns = {"s": "http://soap.sforce.com/2006/04/metadata"}
    c = Counter()
    for t in root.findall("s:types", ns):
        name = t.find("s:name", ns).text
        c[name] = len(t.findall("s:member", ns))
    total = sum(c.values()) or 1
    return [(k, v/total) for k, v in c.most_common()]

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    target = int(sys.argv[1])
    shape  = SHAPE
    if "--shape" in sys.argv:
        shape = shape_from(sys.argv[sys.argv.index("--shape")+1])
    rnd = random.Random(20260925)

    # lines = 4 header/footer + 3 per types block + 1 per member
    n_members = max(1, target - 4 - 3*len(shape))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<Package xmlns="http://soap.sforce.com/2006/04/metadata">']
    made = 0
    for i, (t, frac) in enumerate(shape):
        k = n_members - made if i == len(shape)-1 else int(round(n_members*frac))
        if k <= 0: continue
        out.append("    <types>")
        seen = set()
        j = 0
        while len(seen) < k:
            m = member(t, rnd, j); j += 1
            if m in seen: continue
            seen.add(m); out.append(f"        <members>{m}</members>")
        out.append(f"        <name>{t}</name>")
        out.append("    </types>")
        made += k
    out.append("    <version>67.0</version>")
    out.append("</Package>")
    sys.stdout.write("\n".join(out) + "\n")
    print(f"# {len(out)} lines, {made} components, {len(shape)} types",
          file=sys.stderr)

if __name__ == "__main__":
    main()
