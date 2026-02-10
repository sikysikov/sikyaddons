# -*- coding: utf-8 -*-
import xbmc
import re
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, quote, unquote_plus
from urllib.request import Request, urlopen

PORT = 32541
TMDB_KEY = 'f090bb54758cabf231fb605d3e3e0468'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Accept': 'application/json'}

def log(msg):
    xbmc.log('CSFD_TMDB_SERVICE: {}'.format(msg), level=xbmc.LOGDEBUG)

class TMDBRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): return

    def do_GET(self):
        try:
            parsed_path = urlparse(self.path)
            query = parse_qs(parsed_path.query)
            
            csfd_id = query.get("csfd_id", [None])[0]
            year = query.get("year", [""])[0]
            
            orig_title = query.get("orig", [""])[0]
            if orig_title:
                orig_title = unquote_plus(orig_title)

            log('Zpracovávám: {} | {} ({})'.format(csfd_id, orig_title, year))
            
            xml_data = self.process_request(csfd_id, orig_title, year)
            
            self.send_response(200)
            self.send_header('Content-type', 'text/xml; charset=utf-8')
            self.end_headers()
            self.wfile.write(xml_data.encode('utf-8'))
            
        except Exception as e:
            log('Kritická chyba v RequestHandleru: {}'.format(e))
            self.send_response(500)
            self.end_headers()

    def search_tmdb(self, title, year):
        if not title: return None
        try:
            search_term = title.split(' / ')[0].split(' - ')[0].strip()
            url = 'https://api.themoviedb.org/3/search/movie?api_key={}&query={}&language=cs-CZ'.format(TMDB_KEY, quote(search_term))
            if year and len(year) == 4:
                url += '&year={}'.format(year)
            
            log('Hledám na TMDB: {}'.format(url))
            res = json.loads(urlopen(Request(url, headers=HEADERS), timeout=5).read())
            
            if res.get('results'):
                log('TMDB ID nalezeno: {}'.format(res['results'][0]['id']))
                return res['results'][0]['id']
            
            return None
        except Exception as e:
            log('TMDB Search Error: {}'.format(e))
            return None

    def process_request(self, csfd_id, title, year):
        tmdb_id = self.search_tmdb(title, year)
        if not tmdb_id and year:
            log('S rokem neúspěšné, zkouším hledat bez roku...')
            tmdb_id = self.search_tmdb(title, None)

        if not tmdb_id:
            return "<details></details>"

        url = 'https://api.themoviedb.org/3/movie/{}?api_key={}&language=cs-CZ&append_to_response=videos,images,credits,release_dates,keywords&include_image_language=cs,en,null'.format(tmdb_id, TMDB_KEY)
        data = json.loads(urlopen(Request(url, headers=HEADERS), timeout=5).read())

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
                eng_res = json.loads(urlopen(Request(eng_url, headers=HEADERS), timeout=5).read())
                for v in eng_res.get('results', []):
                    if v.get('type') == 'Trailer' and v.get('site') == 'YouTube':
                        yt_id = v.get('key'); break
            except: pass

        if yt_id:
            xml_parts.append('<trailer>plugin://plugin.video.youtube/play/?video_id={}</trailer>'.format(yt_id))

        return "".join(xml_parts)

def run_server():
    server_address = ('127.0.0.1', PORT)
    httpd = HTTPServer(server_address, TMDBRequestHandler)
    monitor = xbmc.Monitor()
    log('TMDb Helper Server běží na portu {}'.format(PORT))
    while not monitor.abortRequested():
        httpd.handle_request()
    httpd.server_close()

if __name__ == '__main__':
    run_server()