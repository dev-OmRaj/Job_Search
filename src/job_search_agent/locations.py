from __future__ import annotations

import re


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


# Offline aliases used when a career portal publishes only a city, state, or union
# territory. Multi-word values are retained as phrases to avoid accidental partial
# matches. This intentionally contains employment hubs as well as state capitals and
# commonly used alternate spellings.
INDIA_LOCATION_ALIASES = {
    "india", "bharat",
    # States and union territories
    "andaman and nicobar islands", "andhra pradesh", "arunachal pradesh",
    "assam", "bihar", "chandigarh", "chhattisgarh", "dadra and nagar haveli",
    "daman and diu", "delhi", "goa", "gujarat", "haryana", "himachal pradesh",
    "jammu and kashmir", "jharkhand", "karnataka", "kerala", "ladakh",
    "lakshadweep", "madhya pradesh", "maharashtra", "manipur", "meghalaya",
    "mizoram", "nagaland", "odisha", "orissa", "puducherry", "pondicherry",
    "punjab", "rajasthan", "sikkim", "tamil nadu", "telangana", "tripura",
    "uttar pradesh", "uttarakhand", "uttaranchal", "west bengal",
    # Cities and common portal spellings
    "agra", "ahmedabad", "ahmednagar", "aizawl", "ajmer", "akola", "aligarh",
    "allahabad", "alwar", "amaravati", "ambala", "amravati", "amritsar",
    "anand", "anantapur", "asansol", "aurangabad", "ayodhya", "badlapur",
    "balasore", "ballari", "bangalore", "bareilly", "belgaum", "belagavi",
    "bengaluru", "berhampur", "bhagalpur", "bharatpur", "bhatinda", "bathinda",
    "bhavnagar", "bhilai", "bhilwara", "bhiwandi", "bhopal", "bhubaneswar",
    "bhuj", "bidar", "bikaner", "bilaspur", "bokaro", "burdwan", "calcutta",
    "calicut", "chandrapur", "chennai", "chittoor", "coimbatore", "cuttack",
    "darbhanga", "davangere", "dehradun", "deoghar", "dhanbad", "dharamshala",
    "dharwad", "dibrugarh", "durg", "durgapur", "ernakulam", "erode",
    "faridabad", "firozabad", "gandhinagar", "gangtok", "ghaziabad", "gorakhpur",
    "greater noida", "guntur", "gurgaon", "gurugram", "guwahati", "gwalior",
    "haldwani", "haridwar", "hisar", "hosur", "hubballi", "hubli", "hyderabad",
    "imphal", "indore", "itanagar", "jabalpur", "jaipur", "jalandhar", "jalgaon",
    "jammu", "jamnagar", "jamshedpur", "jhansi", "jodhpur", "jorhat", "kakinada",
    "kalaburagi", "kalyan", "kanpur", "kannur", "karnal", "kochi", "cochin",
    "kolar", "kolhapur", "kolkata", "kollam", "kota", "kottayam", "kozhikode",
    "kurnool", "kurukshetra", "leh", "lucknow", "ludhiana", "madurai", "mangalore",
    "mangaluru", "mathura", "meerut", "mohali", "moradabad", "mumbai", "mysore",
    "mysuru", "nagpur", "nashik", "nasik", "navi mumbai", "nellore", "new delhi",
    "noida", "panaji", "panchkula", "panipat", "patiala", "patna", "prayagraj",
    "pune", "puri", "raipur", "rajahmundry", "rajkot", "ranchi", "rourkela",
    "rohtak", "salem", "sambalpur", "satara", "secunderabad", "shimla", "shillong",
    "siliguri", "solapur", "sonipat", "srinagar", "surat", "thane", "thanjavur",
    "thiruvananthapuram", "thrissur", "tiruchirappalli", "tirunelveli", "tirupati",
    "tiruppur", "trichy", "trivandrum", "udaipur", "ujjain", "vadodara", "baroda",
    "varanasi", "vellore", "vijayawada", "visakhapatnam", "vizag", "warangal",
}

_NORMALIZED_INDIA_ALIASES = frozenset(_normalize(value) for value in INDIA_LOCATION_ALIASES)


def is_india_location(location: str) -> bool:
    """Return true when a location explicitly identifies an Indian place offline."""

    normalized = _normalize(location)
    if not normalized:
        return False
    padded = f" {normalized} "
    if any(f" {alias} " in padded for alias in _NORMALIZED_INDIA_ALIASES):
        return True

    # Treat ISO country codes as codes only when they are standalone comma/semicolon
    # components. This avoids interpreting the ordinary English word "in" as India.
    components = {
        _normalize(component)
        for component in re.split(r"[,;/|]", location)
        if _normalize(component)
    }
    return bool(components & {"in", "ind"})
