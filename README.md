# JsonToDjangoModel
This script converts a JSON structure (given as input from a file) to a django model. 
It deduces field types reasonably well, and has a variety of methods to handle nested objects and nested object arrays. Any value can, and an array or primitives (like "values":[1,2,3]) must,
be stored in a django JSONField, which is shared between all fields that are interpreted in this manner.
This project was created because I wanted to make a website that relied on the ClashRoyale developer API, which gives responses in JSON, but I didn't want to tediously
copy everything manually.
