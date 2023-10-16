import csv
from OSMPythonTools.nominatim import Nominatim

nominatim = Nominatim()

results = []

with open("rosters_2023-24.csv", "r") as input_file:
    reader = csv.DictReader(input_file)

    for row in reader:
        try:
            result = nominatim.query(row['hometown'])
            json = result._json
            if len(json) > 1:
                reverse = nominatim.query(json[0]['lat'], json[0]['lon'], reverse=True, zoom=16, addressdetails=1, accept-language='en')
                lookup = reverse.address()
            else:
                lookup = json
            if 'town' in lookup:
                row['city'] = lookup['town']
            elif 'city' in lookup:
                row['city'] = lookup['city']
            else:
                row['city'] = None
            if 'county' in lookup:
                row['county'] = lookup['county']
            else:
                row['county'] = None
            if 'state' in lookup:
                row['state'] = lookup['state']
            else:
                row['state'] = None
            if 'country' in lookup:
                row['country'] = lookup['country']
            else:
                row['country'] = 'United States'
            results.append(row)
        except:
            print(lookup)
            raise


with open("rosters_2023-24_standard.csv", "w") as output_file:
    writer = csv.DictWriter(output_file, fieldnames = ['ncaa_id','team','player_id','name','year','hometown','high_school','previous_school','height','position','jersey','url','season', 'city', 'county', 'state', 'country'])
    writer.writerows(results)
