# -*- coding: utf-8 -*-
import xbmc
import xbmcaddon
import re
import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, quote
from urllib.request import Request, urlopen

PORT = 32541
TMDB_KEY = 'f090bb54758cabf231fb605d3e3e0468'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', 'Accept': 'application/json'}

def log(msg):
    xbmc.log('CSFD_TMDB_SERVICE: {}'.format(msg), level=xbmc.LOGDEBUG)

class TMDBRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed_path = urlparse(self.path)
        query = parse_qs(parsed_path.query)
        csfd_id_param = query.get("csfd_id", [None])[0]

        if not csfd_id_param:
            self.send_error(400, "Missing csfd_id")
            return

        log('Zpracovávám rozšířený požadavek (vč. posterů a studií) pro CSFD ID: {}'.format(csfd_id_param))
        
        try:
            xml_data = self.process_request(csfd_id_param)
            self.send_response(200)
            self.send_header('Content-type', 'text/xml; charset=utf-8')
            self.end_headers()
            self.wfile.write(xml_data.encode('utf-8'))
        except Exception as e:
            log('Kritická chyba: {}'.format(e))
            self.send_response(500)
            self.end_headers()

    def extract_numeric_id(self, val):
        match = re.search(r'(\d+)', str(val))
        return match.group(1) if match else None

    def get_movie_info(self, csfd_id):
        num_id = self.extract_numeric_id(csfd_id)
        if not num_id: return None, None
        try:
            url = 'https://www.csfd.cz/film/{}/'.format(num_id)
            req = Request(url, headers=HEADERS)
            html = urlopen(req, timeout=7).read().decode('utf-8')
            title = None
            for pattern in [r'<h1[^>]*>\s*([^<(\n]+)', r'<title>([^|<]+)']:
                match = re.search(pattern, html)
                if match:
                    title = match.group(1).strip()
                    break
            year_match = re.search(r'<span>\((\d{4})\)</span>', html)
            year = year_match.group(1) if year_match else None
            return title, year
        except:
            return None, None

    def search_tmdb(self, title, year):
        try:
            url = 'https://api.themoviedb.org/3/search/movie?api_key={}&query={}&language=cs-CZ'.format(TMDB_KEY, quote(title))
            if year: url += '&year={}'.format(year)
            res = json.loads(urlopen(Request(url, headers=HEADERS), timeout=5).read())
            return res['results'][0]['id'] if res.get('results') else None
        except:
            return None

    def process_request(self, csfd_id):
        title, year = self.get_movie_info(csfd_id)
        if not title: return "<details></details>"
        
        tmdb_id = self.search_tmdb(title, year)
        if not tmdb_id: return "<details></details>"

        url = 'https://api.themoviedb.org/3/movie/{}?api_key={}&language=cs-CZ&append_to_response=videos,images,credits&include_image_language=cs,en,null'.format(tmdb_id, TMDB_KEY)
        data = json.loads(urlopen(Request(url, headers=HEADERS), timeout=5).read())

        xml_parts = []
        
        # 1. FANARTY (Backdrops)
        if data.get('images', {}).get('backdrops'):
            xml_parts.append('<fanart>')
            for img in sorted(data['images']['backdrops'], key=lambda x: x.get('vote_average', 0), reverse=True)[:10]:
                path = 'https://image.tmdb.org/t/p/original{}'.format(img['file_path'])
                xml_parts.append('<thumb>{}</thumb>'.format(path.replace('&', '&amp;')))
            xml_parts.append('</fanart>')

        # 2. POSTERY
        if data.get('images', {}).get('posters'):
            for img in sorted(data['images']['posters'], key=lambda x: x.get('vote_average', 0), reverse=True)[:10]:
                path = 'https://image.tmdb.org/t/p/original{}'.format(img['file_path'])
                xml_parts.append('<thumb aspect="poster">{}</thumb>'.format(path.replace('&', '&amp;')))

        # 3. STUDIA A LOGA (Production Companies)
        if data.get('production_companies'):
            for studio in data['production_companies']:
                s_name = studio.get('name', '').replace('&', '&amp;')
                xml_parts.append('<studio>{}</studio>'.format(s_name))
                if studio.get('logo_path'):
                    logo_url = 'https://image.tmdb.org/t/p/original{}'.format(studio['logo_path'])
                    xml_parts.append('<studio_logo>{}</studio_logo>'.format(logo_url))

        # 4. HERCI
        if data.get('credits', {}).get('cast'):
            for actor in data['credits']['cast'][:15]:
                name = actor.get('name', '').replace('&', '&amp;')
                role = actor.get('character', '').replace('&', '&amp;')
                thumb = 'https://image.tmdb.org/t/p/h632{}'.format(actor['profile_path']) if actor.get('profile_path') else ''
                xml_parts.append('<actor><name>{}</name><role>{}</role><thumb>{}</thumb></actor>'.format(name, role, thumb))

        # 5. TRAILER s fallbackem
        yt_id = None
        for v in data.get('videos', {}).get('results', []):
            if v.get('type') == 'Trailer' and v.get('site') == 'YouTube':
                yt_id = v.get('key')
                break
        
        if not yt_id:
            try:
                eng_url = 'https://api.themoviedb.org/3/movie/{}/videos?api_key={}&language=en-US'.format(tmdb_id, TMDB_KEY)
                eng_res = json.loads(urlopen(Request(eng_url, headers=HEADERS), timeout=5).read())
                for v in eng_res.get('results', []):
                    if v.get('type') == 'Trailer' and v.get('site') == 'YouTube':
                        yt_id = v.get('key')
                        break
            except: pass

        if yt_id:
            xml_parts.append('<trailer>plugin://plugin.video.youtube/play/?video_id={}</trailer>'.format(yt_id))

        return "".join(xml_parts)

def run_server():
    server_address = ('127.0.0.1', PORT)
    httpd = HTTPServer(server_address, TMDBRequestHandler)
    httpd.timeout = 1
    monitor = xbmc.Monitor()
    log('Server nastartován na portu {}'.format(PORT))
    while not monitor.abortRequested():
        httpd.handle_request()
    httpd.server_close()

if __name__ == '__main__':
    run_server()