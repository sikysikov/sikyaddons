# -*- coding: utf-8 -*-
import xbmc
import re
import json
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, quote, unquote_plus
from urllib.request import Request, urlopen

PORT = 32541
TMDB_KEY = 'f090bb54758cabf231fb605d3e3e0468'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Accept': 'application/json'}

def log(msg):
    xbmc.log('CSFD_TMDB_SERVICE: {}'.format(msg), level=xbmc.LOGDEBUG)

class TMDBRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): return

    def parse_request(self):
        self.command = None
        self.request_version = version = "HTTP/0.9"
        self.close_connection = True
        try:
            raw = self.raw_requestline
            if len(raw) > 65536:
                self.requestline = ''
                self.request_version = ''
                self.command = ''
                self.send_error(414)
                return False

            requestline = raw.decode('utf-8', 'replace')
            requestline = requestline.rstrip('\r\n')
            self.requestline = requestline

            words = requestline.split()
            if len(words) == 0:
                return False

            if len(words) >= 3:
                self.command = words[0]
                self.path = words[1]
                self.request_version = words[-1]
                if not self.request_version.startswith('HTTP/'):
                    self.send_error(400, "Bad request version")
                    return False
            elif len(words) == 2:
                self.command, self.path = words
                self.close_connection = True
                if self.command != 'GET':
                    self.send_error(400, "Bad HTTP/0.9 request type")
                    return False
            
            self.headers = http.client.parse_headers(self.rfile, _class=self.MessageClass)
            return True

        except Exception as e:
            log('Chyba v parse_request: {}'.format(e))
            self.send_error(400, "Bad request processing")
            return False

    def do_GET(self):
        try:
            raw_path = unquote_plus(self.path)
            
            csfd_id = self.get_param(raw_path, 'csfd_id')
            year = self.get_param(raw_path, 'year')
            orig_title = self.get_param(raw_path, 'orig')
            cz_title = self.get_param(raw_path, 'title')

            log('Požadavek: {} | Originál: "{}" | Hlavní název: "{}" ({})'.format(csfd_id, orig_title, cz_title, year))
            
            xml_data = self.process_request(csfd_id, orig_title, cz_title, year)
            
            self.send_response(200)
            self.send_header('Content-type', 'text/xml; charset=utf-8')
            self.end_headers()
            self.wfile.write(xml_data.encode('utf-8'))
            
        except Exception as e:
            log('Chyba v do_GET: {}'.format(e))
            self.send_response(500)
            self.end_headers()

    def get_param(self, path, name):
        match = re.search(r'[?&]' + name + r'=([^&]*)', path)
        return match.group(1) if match else ""

    def search_tmdb(self, title, year):
        if not title or title == "None": return None
        try:
            search_term = title.split(' / ')[0].split(' - ')[0].strip()
            encoded_title = quote(search_term)
            
            url = 'https://api.themoviedb.org/3/search/movie?api_key={}&query={}&language=cs-CZ'.format(TMDB_KEY, encoded_title)
            if year and len(str(year)) == 4:
                url += '&year={}'.format(year)
            
            log('TMDB API Request: {}'.format(url))
            
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=5) as response:
                res = json.loads(response.read().decode('utf-8'))
            
            if res.get('results'):
                return res['results'][0]['id']
            return None
        except Exception as e:
            log('Search Error ({}): {}'.format(title, e))
            return None

    def process_request(self, csfd_id, orig_title, cz_title, year):
        tmdb_id = None

        # 1. Originální název + rok
        tmdb_id = self.search_tmdb(orig_title, year)
        
        # 2. Hlavní název + rok
        if not tmdb_id and cz_title:
            log('Hledání podle originálu neúspěšné, zkouším český název...')
            tmdb_id = self.search_tmdb(cz_title, year)

        # 3. Originální název BEZ roku
        if not tmdb_id:
            log('Hledání s rokem neúspěšné, zkouším bez roku...')
            tmdb_id = self.search_tmdb(orig_title, None)

        if not tmdb_id:
            log('Film nenalezen ani jednou metodou.')
            return "<details></details>"

        try:
            url = 'https://api.themoviedb.org/3/movie/{}?api_key={}&language=cs-CZ&append_to_response=videos,images,credits,release_dates,keywords&include_image_language=cs,en,null'.format(tmdb_id, TMDB_KEY)
            req = Request(url, headers=HEADERS)
            data = json.loads(urlopen(req, timeout=5).read().decode('utf-8'))

            xml_parts = []
            
            # --- UNIQUE IDs ---
            tmdb_id_val = data.get('id')
            imdb_id_val = data.get('imdb_id')
            if tmdb_id_val:
                xml_parts.append('<uniqueid type="tmdb" default="true">{}</uniqueid>'.format(tmdb_id_val))
            if imdb_id_val:
                xml_parts.append('<uniqueid type="imdb">{}</uniqueid>'.format(imdb_id_val))

            # --- TAGLINE ---
            tagline = data.get('tagline', '')
            if tagline:
                xml_parts.append('<tagline>{}</tagline>'.format(tagline.replace('&', '&amp;')))

            # --- PREMIERED ---
            if data.get('release_date'):
                xml_parts.append('<premiered>{}</premiered>'.format(data['release_date']))

            # --- KOLEKCE / SET ---
            if data.get('belongs_to_collection'):
                set_name = data['belongs_to_collection'].get('name', '')
                xml_parts.append('<set>{}</set>'.format(set_name.replace('&', '&amp;')))

            # --- MPAA / CERTIFIKACE ---
            mpaa_val = ""
            releases = data.get('release_dates', {}).get('results', [])
            for country_code in ['CZ', 'US']:
                for r in releases:
                    if r.get('iso_3166_1') == country_code:
                        dates = r.get('release_dates', [])
                        if dates:
                            mpaa_val = dates[0].get('certification', '')
                    if mpaa_val: break
                if mpaa_val: break
            if mpaa_val:
                xml_parts.append('<mpaa>{}</mpaa>'.format(mpaa_val))

            # --- TAGY / KEYWORDS ---
            keywords = data.get('keywords', {}).get('keywords', [])
            for k in keywords[:10]: # limit na 10 tagů
                xml_parts.append('<tag>{}</tag>'.format(k.get('name', '').replace('&', '&amp;')))

            # --- HODNOCENÍ ---
            vote_avg = data.get('vote_average', 0)
            vote_cnt = data.get('vote_count', 0)
            if vote_cnt > 0:
                xml_parts.append('<rating name="themoviedb" max="10" default="false">{}</rating>'.format(vote_avg))
                xml_parts.append('<votes>{}</votes>'.format(vote_cnt))

            # --- CLEARLOGO ---
            if data.get('images', {}).get('logos'):
                logos = sorted(data['images']['logos'], key=lambda x: x.get('vote_average', 0), reverse=True)
                if logos:
                    logo_path = logos[0]['file_path']
                    xml_parts.append('<clearlogo>https://image.tmdb.org/t/p/original{}</clearlogo>'.format(logo_path))
                    xml_parts.append('<thumb aspect="clearlogo">https://image.tmdb.org/t/p/original{}</thumb>'.format(logo_path))

            # --- FANARTY ---
            if data.get('images', {}).get('backdrops'):
                xml_parts.append('<fanart>')
                for img in sorted(data['images']['backdrops'], key=lambda x: x.get('vote_average', 0), reverse=True)[:10]:
                    xml_parts.append('<thumb>https://image.tmdb.org/t/p/original{}</thumb>'.format(img['file_path']))
                xml_parts.append('</fanart>')

            # --- POSTERY ---
            if data.get('images', {}).get('posters'):
                for img in sorted(data['images']['posters'], key=lambda x: x.get('vote_average', 0), reverse=True)[:5]:
                    xml_parts.append('<thumb aspect="poster">https://image.tmdb.org/t/p/original{}</thumb>'.format(img['file_path']))

            # --- STUDIA ---
            if data.get('production_companies'):
                for studio in data['production_companies']:
                    s_name = studio.get('name', '').replace('&', '&amp;')
                    xml_parts.append('<studio>{}</studio>'.format(s_name))
                    if studio.get('logo_path'):
                        xml_parts.append('<studio_logo>https://image.tmdb.org/t/p/original{}</studio_logo>'.format(studio['logo_path']))

            # --- HERCI ---
            if data.get('credits', {}).get('cast'):
                for actor in data['credits']['cast'][:15]:
                    name = actor.get('name', '').replace('&', '&amp;')
                    role = actor.get('character', '').replace('&', '&amp;')
                    thumb = 'https://image.tmdb.org/t/p/h632{}'.format(actor['profile_path']) if actor.get('profile_path') else ''
                    xml_parts.append('<actor><name>{}</name><role>{}</role><thumb>{}</thumb></actor>'.format(name, role, thumb))

            # --- TRAILER ---
            yt_id = None
            for v in data.get('videos', {}).get('results', []):
                if v.get('type') == 'Trailer' and v.get('site') == 'YouTube':
                    yt_id = v.get('key'); break
            
            if not yt_id:
                try:
                    eng_url = 'https://api.themoviedb.org/3/movie/{}/videos?api_key={}&language=en-US'.format(tmdb_id, TMDB_KEY)
                    eng_res = json.loads(urlopen(Request(eng_url, headers=HEADERS), timeout=5).read().decode('utf-8'))
                    for v in eng_res.get('results', []):
                        if v.get('type') == 'Trailer' and v.get('site') == 'YouTube':
                            yt_id = v.get('key'); break
                except: pass

            if yt_id:
                xml_parts.append('<trailer>plugin://plugin.video.youtube/play/?video_id={}</trailer>'.format(yt_id))

            return "".join(xml_parts)
        except Exception as e:
            log('Chyba při zpracování: {}'.format(e))
            return "<details></details>"

def run_server():
    server_address = ('127.0.0.1', PORT)
    httpd = HTTPServer(server_address, TMDBRequestHandler)
    httpd.timeout = 1 
    monitor = xbmc.Monitor()
    log('TMDb Helper Server běží na portu {}'.format(PORT))
    while not monitor.abortRequested():
        httpd.handle_request()
    httpd.server_close()

if __name__ == '__main__':
    run_server()