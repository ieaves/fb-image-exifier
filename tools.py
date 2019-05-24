import os
import shutil
import requests
import piexif
import datetime
import abc
import fractions
import math
import json


def get_json_files(directory):
    direc = os.path.join(directory, 'album')
    files = [os.path.join(direc, f) for f in os.listdir(direc) if f.endswith('json')]
    return files


def load_json(json_file, directory):
    with open(json_file, 'r') as f:
        json_result = json.load(f)

    json_result['photos'] = build_absolute_uris(json_result['photos'], directory)
    return json_result


def build_absolute_uris(json_list, directory):
    for js in json_list:
        js['uri'] = os.path.join(directory, js['uri'])
    return json_list


def build_json_objs(directory):
    json_files = get_json_files(directory)
    subdir = os.path.dirname(directory)
    json_objs = [load_json(file, subdir) for file in json_files]
    return json_objs


def get_ip_info(ip):
    url = f'https://ipapi.co/{ip}/json/'
    resp = requests.get(url)
    if not resp.ok:
        raise Exception
    return resp.json()


class exif_obj:
    def __init__(self, exif_field, exif_id):
        self.exif_field = exif_field
        self.exif_id = exif_id

    def update(self, exif, value, overwrite=False):
        if overwrite or self.exif_id not in exif[self.exif_field]:
            exif[self.exif_field][self.exif_id] = self.encoder(value)
        return exif

    @abc.abstractmethod
    def encoder(self, value):
        pass


class timestamp(exif_obj):
    def encoder(self, value):
        dt_format = "%Y:%m:%d %H:%M:%S"
        dt = datetime.datetime.fromtimestamp(value)
        result = dt.strftime(dt_format).encode('utf-8')
        return result


class coordinate():
    def __init__(self, exif_id):
        self.exif_field = "GPS"
        self.exif_id = exif_id
        if self.exif_id == piexif.GPSIFD.GPSLatitude:
            self.exif_ref_id = piexif.GPSIFD.GPSLatitudeRef
            self.direction = ['N', 'S']
        elif self.exif_id == piexif.GPSIFD.GPSLongitude:
            self.exif_ref_id = piexif.GPSIFD.GPSLongitudeRef
            self.direction = ['E', 'W']
        else:
            raise Exception

        self.direction = [d.encode('utf-8') for d in self.direction]

    def update(self, exif, value, overwrite=False):
        if overwrite or self.exif_id not in exif[self.exif_field]:
            res = self.encoder(abs(value))
            exif[self.exif_field][self.exif_ref_id] = self.direction[value < 0]
            exif[self.exif_field][self.exif_id] = res
        return exif

    def encoder(self, value):
        dms = self.dec_deg_to_dms(value)
        return self.rational_dms(dms)

    @staticmethod
    def dec_deg_to_dms(degrees):
        degs = degrees // 1
        degrees = (degrees - degs) * 60
        mins = degrees // 1
        secs = (degrees - mins) * 60
        return degs, mins, secs

    @staticmethod
    def rational_dms(dms_tup):
        val_range = 4294967295
        res = [fractions.Fraction.from_float(x).limit_denominator(int(val_range / max(math.ceil(x), 1)))
               for x in dms_tup]

        res = tuple((r.numerator, r.denominator) for r in res)
        return res


class location_inferer:
    def __init__(self):
        self.ip_map = {}

    @staticmethod
    def get_ip(photo_meta):
        try:
            ip_addr = photo_meta['media_metadata']['photo_metadata']['upload_ip']
        except:
            ip_addr = None
        return ip_addr

    def get(self, ip, attribute=None):
        if ip not in self.ip_map:
            self.ip_map[ip] = get_ip_info(ip)

        if not attribute:
            return self.ip_map[ip]
        else:
            return self.ip_map[ip][attribute]


_metadata_map = {'creation_timestamp': timestamp('Exif', piexif.ExifIFD.DateTimeOriginal),
                 'latitude': coordinate(piexif.GPSIFD.GPSLatitude),
                 'longitude': coordinate(piexif.GPSIFD.GPSLongitude),}


def parse_metadata(metadata, loc_inferrer):
    album_name = metadata['name']

    new_meta = []
    for item in metadata['photos']:
        res = {'uri': item['uri']}
        exif_data = {attr: item[attr] for attr in _metadata_map.keys() if attr in item}

        ip = loc_inferrer.get_ip(item)
        if ip:
            exif_data['latitude'] = exif_data.get('latitude', loc_inferrer.get(ip, 'latitude'))
            exif_data['longitude'] = exif_data.get('longitude', loc_inferrer.get(ip, 'longitude'))

        res['exif_data'] = exif_data
        new_meta.append(res)

    return {'album_name': album_name, 'metadata': new_meta}


def get_updated_exif(meta):
    original_exif = piexif.load(meta['uri'])
    updated_exif = original_exif.copy()
    for key, updater in _metadata_map.items():
        if key in meta['exif_data']:
            value = meta['exif_data'][key]
            updater.update(updated_exif, value)
    return updated_exif


def update_exif(meta):
    updated_exif = get_updated_exif(meta)
    exif_bytes = piexif.dump(updated_exif)
    piexif.insert(exif_bytes, meta['uri'])


def update_metas(json_objs):
    for i, metadata in enumerate(json_objs):
        print(i)
        for parsed_meta in parse_metadata(metadata):
            update_exif(parsed_meta)


def write_updated_images(parsed_meta, output_folder):
    final_dir = os.path.join(output_folder, parsed_meta['album_name'])
    os.makedirs(final_dir, exist_ok=True)
    for meta in parsed_meta['metadata']:
        curr_dir = os.getcwd()
        inp_file = os.path.join(curr_dir, meta['uri'])
        out_file = os.path.join(curr_dir, final_dir, os.path.basename(inp_file))
        temp_meta = meta.copy()

        temp_meta['uri'] = out_file
        shutil.copy2(inp_file, out_file)
        update_exif(temp_meta)


def update_all(json_objs, loc_inferrer, output_folder):
    if os.path.exists(output_folder):
        shutil.rmtree(output_folder)
    for i, metadata in enumerate(json_objs):

        parsed_meta = parse_metadata(metadata, loc_inferrer)
        write_updated_images(parsed_meta, output_folder)


"""
json_objs = build_json_objs(directory)
loc_inferrer = location_inferer()
output_folder = 'temp'
update_all(json_objs, loc_inferrer, output_folder)
"""
