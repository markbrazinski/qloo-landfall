"""P1-3 (UI-V4-ALIGNMENT-PLAN): the retail concept describes the shop trial, never the brand's own product copy."""
from landfall import specialist


def test_retail_concept_never_the_brand_description():
    brand_copy = {"name": "Casual Shirt Brand", "line": "Stylish, wrinkle-free casual shirts for men and women, plus a full "
                  "range of contemporary clothing and accessories.", "source": "MODEL"}
    got = specialist.retail_concept(brand_copy, "Clark Street Mercantile")
    assert got["name"] == "A shelf trial at Clark Street Mercantile" and "Clark Street Mercantile" in got["line"]
    assert specialist.retail_concept(brand_copy)["name"] == specialist.METHOD_FALLBACK["retail"][0]
    trial = {"name": "An 8-week makers-shelf trial at Kettle & Crane Grocers", "line": "60 cases on the makers shelf."}
    assert specialist.retail_concept(trial, "Kettle & Crane") is trial
    # stored dossiers read right too, and other methods are untouched
    d = {"method": "retail", "concept": brand_copy, "candidates": [{"name": "Rooney", "decision": "KEPT"}]}
    assert specialist.concept_of(d)["name"] == "A shelf trial at Rooney"
    assert specialist.concept_of({**d, "method": "popup"}) is brand_copy
