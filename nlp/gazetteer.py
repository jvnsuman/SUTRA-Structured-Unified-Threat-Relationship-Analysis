"""
nlp/gazetteer.py

Curated word lists for the Indian-FIR rule layer (nlp/extraction.py).

Why hand-curated lists instead of only a statistical model: the small
English spaCy model regularly mislabels Indian names and places (a
village tagged PERSON, a nickname tagged LOCATION, an ISBT tagged ORG).
These lists are deliberately short, auditable and easy to extend; they
are used as *corrections and cues* on top of spaCy, never as the only
signal. Every list is lower-case, ASCII-folded unless noted.

Extending: add entries in the relevant set below. Nothing else needs to
change.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Personal names
# --------------------------------------------------------------------------

FIRST_NAMES = frozenset({
    "aarti", "abdul", "abhishek", "aditya", "ajay", "ajit", "akash", "akhilesh", "alok",
    "aman", "amit", "amita", "amrita", "anand", "anil", "anita", "anjali", "ankit",
    "ankita", "anuj", "anup", "arjun", "arun", "arvind", "asha", "ashok", "ashish",
    "babita", "balram", "bharat", "bhavna", "bhim", "bhupendra", "chandan", "chandra",
    "deepa", "deepak", "dev", "devendra", "dharmendra", "dinesh", "durga", "farhan",
    "faizan", "gaurav", "geeta", "gopal", "govind", "guddi", "hari", "harish", "hemant",
    "imran", "irfan", "isha", "jagdish", "jai", "jaya", "jitendra", "juhi", "kailash",
    "kajal", "kamal", "kamla", "kavita", "kiran", "kishan", "kishore", "komal", "krishna",
    "kumar", "lakshmi", "lalita", "lata", "mahesh", "mamta", "manish", "manoj", "meena",
    "meera", "mohan", "mohammad", "mohammed", "mohd", "mukesh", "muhammad", "mustafa",
    "nand", "naresh", "neha", "nikhil", "nisha", "pankaj", "pooja", "poonam", "prakash",
    "pramod", "prem", "priya", "puja", "pushpa", "rahul", "raj", "rajesh", "rajendra",
    "raju", "rakesh", "ram", "ramesh", "ranjit", "rashid", "ravi", "rekha", "rinku",
    "ritu", "rohit", "roshan", "sachin", "sandeep", "sangeeta", "sanjay", "sanjeev",
    "santosh", "sarita", "saroj", "satish", "seema", "shabnam", "shahid", "shakti",
    "shanti", "shiv", "shivani", "shyam", "sita", "smita", "sonia", "sudhir", "sujata",
    "sunil", "sunita", "suraj", "suresh", "sushma", "swati", "tarun", "usha", "vijay",
    "vikas", "vikram", "vimla", "vinay", "vinod", "vipin", "vishal", "yogesh", "zubair",
    "salman", "sameer", "shabana", "sultan", "tabassum", "nasreen", "rubina", "parveen",
    "gurpreet", "harpreet", "jaspreet", "manpreet", "sukhwinder", "balwinder", "kuldeep",
    "lakhan", "mangal", "madan", "narayan", "om", "pawan", "radha", "rani", "sarojini",
    "savita", "sohan", "sumit", "sushil", "tulsi", "uday", "umesh", "vandana", "veena",
})

SURNAMES = frozenset({
    "agarwal", "ahmad", "ahmed", "akhtar", "ali", "ansari", "banerjee", "bansal", "bhatt",
    "bisht", "chandra", "chauhan", "chaudhary", "chaudhry", "chopra", "das", "devi",
    "dubey", "dwivedi", "gandhi", "gill", "goswami", "gupta", "hussain", "iyer", "jain",
    "jha", "joshi", "kapoor", "kashyap", "khan", "khatun", "kumar", "kumari", "kushwaha",
    "lal", "mahato", "malik", "mandal", "mehta", "mishra", "mondal", "mukherjee", "nair",
    "narayan", "pandey", "pandit", "pal", "paswan", "patel", "patil", "prasad", "qureshi",
    "rai", "rana", "rao", "rathore", "reddy", "saini", "sahu", "shah", "sharma", "shaikh",
    "sheikh", "shukla", "singh", "sinha", "solanki", "srivastava", "tiwari", "thakur",
    "tripathi", "verma", "yadav", "zaidi", "bhardwaj", "chatterjee", "dey", "ghosh",
    "sen", "roy", "sarkar", "biswas", "khatoon", "begum", "bibi", "rajput", "meena",
    "gurjar", "jatav", "kori", "nishad", "maurya", "kurmi", "teli", "sonkar", "rawat",
})

# Nicknames / aliases commonly used as "urf" names in Indian police records.
NICKNAMES = frozenset({
    "babu", "bablu", "bhola", "bunty", "chintu", "chotu", "golu", "guddu", "guddi",
    "kallu", "lala", "lallu", "monu", "munna", "pappu", "pinky", "pintu", "raja",
    "rinku", "sonu", "tinku", "tunna", "vicky", "billu", "lucky", "shanu", "sunny",
    "bittu", "dabbu", "gullu", "jugnu", "kalu", "mithu", "nanhe", "pinku", "rocky",
})

# Name-particles / honorifics. HONORIFICS are stripped for matching (they
# are not part of the person's name); NAME_PREFIXES (Mohd. etc.) are part
# of the name but are spelling variants of one another.
HONORIFICS = frozenset({
    "smt", "shri", "shree", "sri", "sh", "mr", "mrs", "ms", "miss", "dr", "km", "kumari",
    "late", "adv", "prof", "er", "haji", "hajji", "maulana", "pt", "pandit", "sardar",
    "inspector", "insp", "si", "asi", "constable", "ct", "hc", "sho", "dsp", "sp",
    "complainant", "accused", "suspect", "victim", "witness", "informant",
})

# Role words that precede a name in reports but are not part of it
# ("Recruiters Monu", "driver Ramesh Kumar"). Trimmed like honorifics.
ROLE_WORDS = frozenset({
    "recruiter", "recruiters", "driver", "transporter", "handler", "caretaker", "keeper", "agent",
    "owner", "proprietor", "manager", "brother", "sister", "father", "mother", "uncle", "aunt",
    "neighbour", "neighbor", "passenger", "passengers", "gang", "member", "members", "associates",
    "associate", "accomplice", "kingpin", "middleman", "dalal", "conductor", "cleaner", "helper",
})

# Ordinary English report vocabulary. When a document is written in ALL
# CAPS these must stay lower-case after "de-shouting", otherwise the NER
# model sees "On Mobile" / "Was Noted" as proper nouns.
COMMON_WORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "by", "for", "with", "from", "into", "onto",
    "near", "outside", "inside", "towards", "toward", "via", "after", "before", "during", "while", "when",
    "where", "which", "who", "whom", "whose", "that", "this", "these", "those", "it", "he", "she", "they",
    "his", "her", "their", "its", "him", "them", "was", "were", "is", "are", "be", "been", "being", "has",
    "had", "have", "having", "will", "would", "shall", "should", "can", "could", "may", "might", "not", "no",
    "also", "then", "later", "both", "all", "each", "one", "two", "three", "as", "than", "but", "if", "so",
    "stated", "reported", "seen", "found", "noted", "observed", "spotted", "arrested", "intercepted",
    "traced", "contacted", "called", "using", "used", "uses", "bearing", "registration", "registered",
    "mobile", "phone", "number", "numbers", "sim", "vehicle", "car", "bike", "tempo", "truck",
    "complainant", "accused", "suspect", "victim", "witness", "informant", "village", "vill", "district",
    "dist", "daughter", "son", "wife", "husband", "taken", "away", "pretext", "job", "last", "opened",
    "source", "information", "indicates", "runs", "operates", "recruitment", "through", "commission",
    "paid", "pay", "alias", "urf", "white", "black", "red", "blue", "surveillance", "report", "meeting",
    "proceeded", "arranged", "trip", "driven", "passengers", "driver", "stayed", "works", "working",
    "questioned", "about", "money", "demanded", "threatened", "transferred", "sent", "received", "unknown",
    "female", "male", "girl", "boy", "man", "woman", "police", "station", "case", "date", "time",
    "known", "as", "r/o", "s/o", "d/o", "w/o", "sh", "smt", "shri",
})

MOHAMMAD_VARIANTS = frozenset({
    "mohd", "md", "mohammad", "mohammed", "muhammad", "mohamed", "muhammed", "mahammad",
})

# --------------------------------------------------------------------------
# Places
# --------------------------------------------------------------------------

CITIES_STATES = frozenset({
    "delhi", "new delhi", "noida", "gurgaon", "gurugram", "faridabad", "ghaziabad",
    "mumbai", "pune", "nagpur", "nashik", "thane", "kolkata", "howrah", "siliguri",
    "chennai", "coimbatore", "madurai", "bengaluru", "bangalore", "mysuru", "mysore",
    "hyderabad", "secunderabad", "visakhapatnam", "vijayawada", "ahmedabad", "surat",
    "vadodara", "rajkot", "jaipur", "jodhpur", "udaipur", "kota", "ajmer", "lucknow",
    "kanpur", "varanasi", "agra", "meerut", "prayagraj", "allahabad", "gorakhpur",
    "bareilly", "aligarh", "mathura", "patna", "gaya", "muzaffarpur", "bhagalpur",
    "darbhanga", "gopalganj", "chhapra", "siwan", "ranchi", "jamshedpur", "dhanbad",
    "bhopal", "indore", "gwalior", "jabalpur", "raipur", "bilaspur", "bhubaneswar",
    "cuttack", "guwahati", "shillong", "imphal", "agartala", "aizawl", "kohima",
    "dehradun", "haridwar", "rishikesh", "shimla", "chandigarh", "amritsar", "ludhiana",
    "jalandhar", "patiala", "srinagar", "jammu", "kochi", "thiruvananthapuram",
    "kozhikode", "goa", "panaji", "mangaluru", "hubli", "belgaum", "sonauli", "raxaul",
    "jogbani", "petrapole", "gorakhpur", "malda", "murshidabad", "nadia", "kishanganj",
    "araria", "purnia", "sitamarhi", "motihari", "bettiah", "ballia", "azamgarh",
    "maharashtra", "gujarat", "rajasthan", "haryana", "punjab", "bihar", "jharkhand",
    "odisha", "assam", "manipur", "nagaland", "mizoram", "tripura", "meghalaya",
    "uttarakhand", "karnataka", "kerala", "telangana", "andhra pradesh", "tamil nadu",
    "west bengal", "uttar pradesh", "madhya pradesh", "chhattisgarh", "himachal pradesh",
    "jammu and kashmir", "nepal", "bangladesh", "dubai", "muscat", "doha", "riyadh",
})

# Generic village names that spaCy often tags PERSON. Only used when the
# text has a location cue ("village X", "r/o X", "resident of X") OR the
# token is in this set and NOT in the person-name lists.
COMMON_VILLAGE_NAMES = frozenset({
    "rampur", "sultanpur", "shahpur", "islampur", "ramnagar", "krishnapur", "bhagwanpur",
    "madhopur", "govindpur", "hariharpur", "lakshmipur", "sonpur", "dhanpur", "bhawanipur",
    "mirzapur", "ghazipur", "jaunpur", "fatehpur", "hamirpur", "raebareli", "barabanki",
    "sitapur", "hardoi", "unnao", "banda", "chitrakoot", "sasaram", "arrah", "buxar",
    "nawada", "jamui", "jehanabad", "aurangabad", "kaimur", "rohtas",
})

# Suffix/prefix words that mark a token sequence as a place.
PLACE_SUFFIXES = frozenset({
    "road", "rd", "nagar", "colony", "chowk", "marg", "street", "lane", "vihar", "puri",
    "bagh", "ganj", "pur", "enclave", "bazar", "bazaar", "market", "junction", "station",
    "airport", "isbt", "chauraha", "crossing", "flyover", "highway", "gali", "mohalla",
    "basti", "tola", "gaon", "village", "block", "phase", "extension", "ext", "park",
    "gate", "stand", "stop", "mandi", "dham", "kunj", "khand", "sadan", "apartments", "heights", "tower",
})

PLACE_CUE_WORDS = frozenset({
    "near", "at", "to", "from", "in", "outside", "inside", "towards", "toward", "via",
    "village", "vill", "town", "city", "district", "dist", "ps", "thana", "r/o", "resident",
    "residing", "native", "belonging", "reached", "arrived", "left", "crossed",
})

# Words that look like a proper noun/org to spaCy but are never an entity
# of interest in an FIR.
NON_ENTITY_ACRONYMS = frozenset({
    "upi", "neft", "rtgs", "imps", "atm", "fir", "ipc", "bns", "bnss", "crpc", "cdr",
    "sim", "imei", "pan", "aadhaar", "aadhar", "gps", "cctv", "dvr", "sms", "otp", "kyc",
    "ps", "sho", "asi", "si", "dsp", "sp", "hc", "ct", "ndps", "pocso", "itpa", "ppe",
    "rs", "inr", "usd", "a/c", "ac", "ifsc", "utr", "gst", "tds", "nri", "ngo", "whatsapp",
    "facebook", "instagram", "telegram", "snapchat", "youtube", "google", "paytm",
    "phonepe", "gpay", "bhim", "swift", "innova", "bolero", "scorpio", "creta", "alto",
    "maruti", "hyundai", "mahindra", "tata", "honda", "bajaj", "hero", "yamaha", "suzuki",
    "toyota", "ford", "tempo", "truck", "bus", "auto", "sedan", "suv", "dzire", "ertiga",
    "wagonr", "activa", "splendor", "pulsar", "royal", "enfield", "ambassador", "sumo",
    "eeco", "omni", "xuv", "thar", "brezza", "baleno", "i20", "verna", "city", "amaze",
    "ambulance", "traveller", "tourist",
})

VEHICLE_MAKES = frozenset({
    "maruti", "suzuki", "hyundai", "mahindra", "tata", "honda", "bajaj", "hero", "yamaha",
    "toyota", "ford", "tvs", "kia", "renault", "nissan", "skoda", "volkswagen", "eicher",
    "ashok", "leyland", "swift", "innova", "bolero", "scorpio", "creta", "alto", "dzire",
    "ertiga", "wagonr", "activa", "splendor", "pulsar", "enfield", "sumo", "eeco", "omni",
    "xuv", "thar", "brezza", "baleno", "verna", "amaze", "tempo", "traveller",
})

# --------------------------------------------------------------------------
# Devanagari (Hindi) — kept as literal Devanagari; matched after NFC normalise
# --------------------------------------------------------------------------

DEVANAGARI_FIRST_NAMES = frozenset({
    "राजू", "राजेश", "रमेश", "सुरेश", "महेश", "दिनेश", "मुकेश", "अनिल", "सुनील", "विजय",
    "संजय", "अजय", "अमित", "राहुल", "रोहित", "मनोज", "प्रमोद", "विनोद", "अशोक", "राम",
    "श्याम", "मोहन", "गोपाल", "कृष्ण", "हरीश", "सतीश", "योगेश", "दीपक", "सचिन", "विकास",
    "प्रिया", "सुनीता", "गीता", "सीता", "रीता", "पूजा", "नेहा", "सोनी", "अनीता", "कविता",
    "सरिता", "मीना", "रेखा", "ममता", "सुषमा", "पुष्पा", "शांति", "लक्ष्मी", "आरती",
    "इरफान", "सलमान", "शाहिद", "इमरान", "फ़रहान", "मोहम्मद", "मोहम्मद", "अब्दुल", "राशिद",
    "सोनू", "मोनू", "गुड्डू", "बबलू", "पप्पू", "मुन्ना", "छोटू", "बंटी", "गोलू", "टिंकू", "रिंकू",
})

DEVANAGARI_SURNAMES = frozenset({
    "कुमार", "शर्मा", "वर्मा", "यादव", "सिंह", "गुप्ता", "मिश्रा", "पांडे", "पाण्डेय",
    "तिवारी", "दुबे", "शुक्ला", "श्रीवास्तव", "चौहान", "ठाकुर", "पटेल", "जैन", "अग्रवाल",
    "देवी", "कुमारी", "खान", "अंसारी", "अली", "अहमद", "पासवान", "राय", "मौर्य", "कुशवाहा",
    "प्रसाद", "साहू", "रावत", "नायर", "रेड्डी", "दास", "सेन", "घोष", "बनर्जी", "जोशी",
})

DEVANAGARI_PLACES = frozenset({
    "दिल्ली", "नई दिल्ली", "नोएडा", "गुड़गांव", "गुरुग्राम", "फरीदाबाद", "गाज़ियाबाद", "गाजियाबाद",
    "मुंबई", "पुणे", "कोलकाता", "चेन्नई", "बेंगलुरु", "हैदराबाद", "जयपुर", "लखनऊ", "कानपुर",
    "वाराणसी", "आगरा", "पटना", "गोपालगंज", "रांची", "भोपाल", "इंदौर", "रामपुर", "सोनौली",
    "रक्सौल", "आनंद विहार", "एमजी रोड", "एम जी रोड", "बस स्टैंड", "रेलवे स्टेशन", "मंदिर मार्ग",
})

DEVANAGARI_PLACE_SUFFIXES = frozenset({
    "रोड", "नगर", "कॉलोनी", "चौक", "मार्ग", "विहार", "पुर", "गंज", "बाज़ार", "बाजार", "स्टेशन",
    "गाँव", "गांव", "मोहल्ला", "बस्ती", "मंडी", "एन्क्लेव",
})

DEVANAGARI_CUE_STOPWORDS = frozenset({
    "को", "ने", "से", "का", "की", "के", "में", "पर", "और", "तथा", "है", "था", "थी", "गया",
    "गई", "देखा", "पाया", "बताया", "कहा", "एक", "यह", "वह", "उस", "इस", "जो", "कि", "भी",
    "नहीं", "नंबर", "मोबाइल", "फोन", "नंबर", "पर", "द्वारा", "अभियुक्त", "आरोपी", "संदिग्ध",
    "शिकायतकर्ता", "गवाह", "वाहन", "गाड़ी", "के", "साथ",
})
