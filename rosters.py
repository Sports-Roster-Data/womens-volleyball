import os
import re
import csv
import subprocess
import json
import argparse
import requests
from requests_html import HTMLSession
import tldextract
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup

driver = webdriver.Firefox()

HEADERS = {'No.': 'jersey', 'Name': 'name', 'NAME': 'name', 'Cl.': 'academic_year', 'Pos.': 'position', 'Ht.': 'height', 'Hometown/High School': 'town', 'Hometown/Last School': 'town', 'Num': 'jersey', 'Yr': 'academic_year', 'Ht': 'height', 'Hometown': 'town', 'High School/Previous School': 'high_school', 'Pos': 'position', 'Hometown/Previous School': 'town', 'Exp.': 'academic_year', 'Number': 'jersey', 'Position': 'position', 'HT.': 'height', 'YEAR': 'academic_year', 'HOMETOWN': 'town', 'LAST SCHOOL': 'high_school', 'Yr.': 'academic_year', 'Hometown/High School/Last School': 'town', 'Class': 'academic_year', 'High school': 'high_school', 'Previous College': 'previous_school', 'Cl.-Exp.': 'academic_year', '#': 'jersey', 'High School': 'high_school', 'Hometown / Previous School': 'town', 'No': "jersey", 'Hometown/High School/Previous School': 'town', 'Hometown / High School / Last College': 'town', 'Year': 'academic_year', 'Height': 'height', 'Previous School': 'high_school', 'Cl': 'academic_year', 'Prev. Coll.': 'previous_school', 'Hgt.': 'height', 'Hometown/ High School': 'town', 'Hometown/High School (Last School)': 'town', 'Hometown/High School (Former School)': 'town', 'Hometown / High School': 'town', 'YR': 'academic_year', 'POS': 'position', 'HT': 'height', 'Player': 'name', 'Hometown/High School/Previous College': 'town', 'Last School/Hometown': 'town', 'NO.': 'jersey', 'NAME': 'name', 'YR.': 'academic_year', 'POS.': 'position', 'HIGH SCHOOL': 'high_school', 'NO': 'jersey', 'HOMETOWN/HIGH SCHOOL': 'town', 'Academic Yr.': 'academic_year', 'Full Name': 'name', 'POSITION': 'position', 'Hometown / Previous School / High School': 'town', 'High School / Previous School': 'high_school', 'Hometown/High School (Previous School)': 'town', 'Hometown/Previous School/Club Team': 'town'}

SEASONS = ['2023-24', '2022-23', '2021-22', '2020-21', '2019-20', '2018-19', '2017-18', '2016-17', '2015-16', '2014-15', '2013-14',
'2012-13', '2011-12', '2010-11', '2009-10', '2008-09', '2007-08', '2006-07', '2005-06', '2004-05', '2003-04',
'2002-03', '2001-02', '2000-01', '1999-00', '1998-99', '1997-98', '1996-97', '1995-96', '1994-95', '1993-94', '1992-93', '1991-92', '1990-91']

class LazyDecoder(json.JSONDecoder):
    def decode(self, s, **kwargs):
        regex_replacements = [
            (re.compile(r'([^\\])\\([^\\])'), r'\1\\\\\2'),
            (re.compile(r',(\s*])'), r'\1'),
        ]
        for regex, replacement in regex_replacements:
            s = regex.sub(replacement, s)
        return super().decode(s, **kwargs)

def fetch_url(url):
    r = requests.get(url)
    return r

def fetch_roster(base_url, season):
    url = base_url + "/roster/" + season
    r = fetch_url(url)
#    if r.history:
#        return None
    return BeautifulSoup(r.text, features="html.parser")

def fetch_wbkb_roster(base_url, season):
    url = base_url.replace('index',season+'/roster?view=list')
    headers = {
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36"
    }
    r = requests.get(url, headers=headers)
    if r.status_code == 404:
        html = None
    else:
        html = BeautifulSoup(r.text, features="html.parser")
    return html

def fetch_baskbl_roster(base_url, season):
    if 'index' in base_url:
        url = base_url.replace('index','roster/?season='+season)
    elif base_url.endswith('w-baskbl'):
        url = base_url+"/"+season+"/roster"
    else:
        url = base_url + 'roster/?season='+season
    headers = {
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.69 Safari/537.36"
    }
    r = requests.get(url, headers=headers)
    if r.status_code == 404:
        url = base_url.replace('index', "/"+season+"/roster")
        r = requests.get(url, headers=headers)
    return BeautifulSoup(r.text, features="html.parser")

def parse_roster_baskbl(team, html, season):
    roster = []
    er = tldextract.extract(team['url'])
    cols = [x.text.strip() for x in html.find('thead').find_all('th') if x.text.strip() if x.text.strip() != '']
    if team['ncaa_id'] == 255:
        cols = [x.text.strip() for x in html.find('thead').find_all('th') if x.text.strip() != 'Social']
    new_cols = [HEADERS[c] for c in cols]
    raw_players = [x for x in html.find('tbody').find_all('tr')]
    for raw_player in raw_players:
        [x.span.decompose() for x in raw_player.find_all('td') if x.find('span')]
        raw_player_list = [x.text.strip() for x in raw_player.find_all('td')]
        raw_player_list[4] = " ".join([x for x in raw_player_list[4].split()])
        if len(raw_player_list) < len(new_cols):
            name = " ".join([x.strip() for x in raw_player.find('a').text.replace("  ","").strip().split()])
            raw_player_list.insert(1, name)
        player_dict = dict(zip(new_cols, raw_player_list))
        if 'high_school' not in player_dict:
            player_dict['town'], player_dict['high_school'] = [x.strip() for x in player_dict['town'].split('/', maxsplit=1)]
        if 'previous_school' not in player_dict:
            player_dict['previous_school'] = None
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': None,
            'name': player_dict['name'],
            'year': player_dict['academic_year'],
            'hometown': player_dict['town'],
            'high_school': player_dict['high_school'],
            'previous_school': player_dict['previous_school'],
            'height': player_dict['height'],
            'position': player_dict['position'],
            'jersey': player_dict['jersey'],
            'url': "https://www."+er.domain+"."+er.suffix+raw_player.find('a')['href'],
            'season': season
        })
    return roster

def parse_roster_wbkb(team, html, season):
    roster = []
    er = tldextract.extract(team['url'])
    if team['ncaa_id'] == 30164:
        headers = html.find_all('table')[1].find_all('tr')[0]
    elif team['ncaa_id'] == 326:
        headers = html.find_all('table')[12].find_all('tr')[0]
    else:
        headers = html.find('table').find_all('tr')[0]
    cols = [x.text.strip() for x in headers if x.text.strip() != '']
    if 'Pronounciation' in cols:
        cols.remove('Pronounciation')
    if 'Club Team' in cols:
        cols.remove('Club Team')
    if 'Major' in cols:
        cols.remove('Major')
    if 'MAJOR' in cols:
        cols.remove('MAJOR')
    if 'Major/Minor' in cols:
        cols.remove('Major/Minor')
    if 'College' in cols:
        cols.remove('College')
    if 'Wt.' in cols:
        cols.remove('Wt.')
    if 'Ltrs.' in cols:
        cols.remove('Ltrs.')
    if 'Pronouns' in cols:
        cols.remove('Pronouns')
    new_cols = [HEADERS[c] for c in cols]
    raw_players = [x for x in html.find('tbody').find_all('tr')]
    for raw_player in raw_players:
        [x.span.decompose() for x in raw_player.find_all('td') if x.find('span')]
        raw_player_list = [x.text.strip() for x in raw_player.find_all('td') if x.text.replace('*','').replace('(she/her/hers)','').replace('she/her/hers','').strip() != '']
        if team['ncaa_id'] == 1340 or team['ncaa_id'] == 760 or team['ncaa_id'] == 510 or team['ncaa_id'] == 227:
            raw_name = raw_player.find('a').text.strip().replace("  "," ").replace("\t","").replace("\r","").replace("\n","").split(' ')
            raw_player_list.insert(1, " ".join([x.strip() for x in raw_name if x !='']))
        if team['ncaa_id'] == 73 and raw_player_list[0] == '43':
            continue
        if team['ncaa_id'] == 114 or team['ncaa_id'] == 1050 or team['ncaa_id'] == 1059 or team['ncaa_id'] == 1199 or team['ncaa_id'] == 22626 or team['ncaa_id'] == 24317 or team['ncaa_id'] == 30037 or team['ncaa_id'] == 341 or team['ncaa_id'] == 1315 or team['ncaa_id'] == 46 or team['ncaa_id'] == 641 or team['ncaa_id'] == 730 or team['ncaa_id'] == 75 or team['ncaa_id'] == 806 or team['ncaa_id'] == 817 or team['ncaa_id'] == 89 or team['ncaa_id'] == 145 or team['ncaa_id'] == 217 or team['ncaa_id'] == 247 or team['ncaa_id'] == 2713 or team['ncaa_id'] == 2798 or team['ncaa_id'] == 28594 or team['ncaa_id'] == 30002 or team['ncaa_id'] == 30225 or team['ncaa_id'] == 467 or team['ncaa_id'] == 567 or team['ncaa_id'] == 569 or team['ncaa_id'] == 137 or team['ncaa_id'] == 715 or team['ncaa_id'] == 779 or team['ncaa_id'] == 808 or team['ncaa_id'] == 8688 or team['ncaa_id'] == 379 or team['ncaa_id'] == 1461:
            raw_player_list = [x.text.strip() for x in raw_player.find_all('td')]
        if len(raw_player_list) == 4:
            raw_player_list = [None] + raw_player_list
        elif len(raw_player_list) < 4:
            print(f"skipping {raw_player_list}")
            continue
        raw_player_list[4] = " ".join([x for x in raw_player_list[4].split()])
        if team['ncaa_id'] == 1036:
            if len(raw_player_list) == len(new_cols):
                raw_player_list.pop()
        if team['ncaa_id'] == 1096:
            if len(raw_player_list) >= len(new_cols):
                raw_player_list.pop()
        if team['ncaa_id'] == 142:
            del raw_player_list[2]
        if any(major in raw_player_list for major in ['Nursing', 'Biology', 'Public Health', 'Exercise Science', 'Pre-Nursing', 'Economics', 'Physical Therapy', 'Psychology','Business Administration','Criminal Justice/Psychology', 'Forensic Science', 'Undecided', 'Management', 'Psychology / Management', 'Political Science', 'Psychology / Pre-Medicine', 'Undeclared', 'Biomedical Engineering', 'Business Marketing', 'Chemistry', 'Business', 'Computer Science', 'Business Management', 'AS', 'BIOE', 'CM', 'AE', 'ME', 'BM', 'CVE', 'BME', 'Information Technology', 'Criminal Justice', 'Elementary Education', 'Special Education', 'Criminal Justice/Sociology', 'Exercise and Sports Science', 'Political Science', 'Exercise Sports Science', 'Accounting', 'Psychology', 'Pre-Physical Therapy', 'MS-Counseling Psychology', 'Business Admin.', 'Pre-Medicine', 'MBA', 'Occupational Therapy', 'Pharmacy', 'Biomedical Sciences/Medical Laboratory Sciences', 'Pharm. & Healthcare Business', 'Biomedical Sciences', 'Exercise Science: Pre-Athletic Training', 'Business Administration: Sport Management', 'Exercise Science: Individual Program of Study', 'Applied Psychology & Human Services', 'Outdoor Education, Leadership, & Tourism: Adventure Education', 'Natural Science', 'Exercise Science: Pre-Physical Therapy', 'Childhood Education', 'Global Studies', 'Healthcare Mgmt.', 'Computer Info. Systems', 'Veterinary Science', 'Psychology & Business', 'Business & Psychology', 'Biology & Spanish', 'History & Spanish', 'Politics/Computer Science', 'Geology', 'Politics/Sociology', 'Economics', 'English/Psychology', 'Education', 'Communication Sciences and Disorders', 'Sociology', 'Social Work', 'Psychology / Criminal Justice', 'Undeclared / -', 'Dental Hygiene', 'Early Education/Psychology', 'Elementary Education/Psychology', 'Sports Management', 'Exploratory', 'Creative Writing and Publishing', 'Administration of Justice', 'Emergency Medical Services Management', 'Sport Psychology', 'Physical Education', 'Athletic Training', 'Physician Assistant', 'Sports and Exercise Psychology', 'Athletic Training', 'Sports Managment', 'Sport Management', 'Film and Interactive Media', 'Biochemistry', 'HSSP', 'Business/HSSP', 'Business/Psychology', 'Business/International and Global Studies', 'Industrial Design', 'Mechanical Engineering', 'Architecture', 'Applied Mathematics', 'Business Analytics and Information Management', 'General Business', 'Forensic Biology', 'Pharmaceutical Business', 'Health Sciences', 'Business Undecided', 'Civil Engineering', 'Health Science', 'Education & Studio Art', 'Political Science & Philosophy', 'Biochemistry & Molecular Biology', 'Education & English', 'Business Economics', 'Global & International Studies & French & Francophone Studies', 'Marketing & Sales', 'Math', 'Sports Broadcasting', 'General Studies', 'Occupational Studies', 'Elementary Education/Mathematics', 'Liberal Studies', 'Communication', 'Elementary Education/Sociology', 'Public Health', 'Elementary Education/History', 'Accounting & Marketing', 'Biology/Biotechnology', 'Liberal Studies/Education', 'Entrepreneurship & Marketing', 'Engineering', 'Finance', 'English', 'Music', 'UX', 'NU', 'BE', 'Pre-Veterinary', 'Forensic Psychology', 'Exercise Physiology', 'Outdoor Education, Leadership, & Tourism', 'Recreation & Sports Mgmt.', 'Liberal Arts', 'French', 'Marketing & Communication', 'Dental Hygiene', 'Early Education/Psychology', 'Mathematics and Computer Technology', 'Pre-Physician Assistant', 'Applied Sciences', 'Computer Information Systems', 'Forensic Chemistry', 'Elementary Education and Psychology',"Pre- Physician's Assistant", 'Professional Communications (Graduate)', 'Environmental Science', 'Welding', 'Business Management & Marketing']):
            if team['ncaa_id'] == 186:
                del raw_player_list[4]
            else:
                raw_player_list.pop()
        if len(raw_player_list) < len(new_cols):
            name = " ".join([x.strip() for x in raw_player.find('a').text.replace("  ","").strip().split()])
            raw_player_list.insert(1, name)
        player_dict = dict(zip(new_cols, raw_player_list))
        if 'high_school' not in player_dict:
            if team['ncaa_id'] == 2713:
                player_dict['high_school'] = None
            else:
                player_dict['town'], player_dict['high_school'] = [x.strip() for x in player_dict['town'].split('/', maxsplit=1)]
        if 'previous_school' not in player_dict:
            player_dict['previous_school'] = None
        #print(player_dict)
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': None,
            'name': player_dict['name'],
            'year': player_dict['academic_year'],
            'hometown': player_dict['town'],
            'high_school': player_dict['high_school'],
            'previous_school': player_dict['previous_school'],
            'height': player_dict['height'],
            'position': player_dict['position'],
            'jersey': player_dict['jersey'],
            'url': "https://www."+er.domain+"."+er.suffix+raw_player.find('a')['href'],
            'season': season
        })
    return roster

def fetch_and_parse_clemson(team, season):
    roster = []
    er = tldextract.extract(team['url'])
    url = team['url'] + "roster/season/" + season[0:4]
    r = fetch_url(url)
    html = BeautifulSoup(r.text, features="html.parser")
    cols = [x.text for x in html.find_all('th') if not x.text in ['MAJOR']]
    cols = cols[0:-4]
    new_cols = [HEADERS[c] for c in cols]
    players = html.find('table').find_all('tr')[1:]
    for player in players:
        raw_player_list = [x.text.strip() for x in player.find_all('td')][0:-2]
        player_dict = dict(zip(new_cols, raw_player_list))
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': None,
            'name': player_dict['name'],
            'year': player_dict['academic_year'],
            'hometown': player_dict['town'],
            'high_school': None,
            'previous_school': None,
            'height': player_dict['height'],
            'position': player_dict['position'],
            'jersey': player_dict['jersey'],
            'url': "https://www."+er.domain+"."+er.suffix+player.find('a')['href'],
            'season': season
        })
    return roster

def fetch_and_parse_vandy(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('#players-table tbody tr'), el => {
     const id = '';
     const name = el.querySelectorAll('td')[1].innerText;
     const year = el.querySelectorAll('td')[4].innerText;
     const height = el.querySelectorAll('td')[3].innerText;
     const position = el.querySelectorAll('td')[2].innerText;
     const hometown = el.querySelectorAll('td')[6].innerText;
     hs_el = el.querySelectorAll('td')[5];
     const high_school = hs_el ? hs_el.innerText : '';
     const previous_school = '';
     const jersey = el.querySelectorAll('td')[0].innerText;
     const url = el.querySelectorAll('td')[1].querySelector('a')['href']
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/season/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_miami(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('#players-table tbody tr'), el => {
     const id = '';
     const name = el.querySelectorAll('td')[1].innerText;
     const year = el.querySelectorAll('td')[4].innerText;
     const height = el.querySelectorAll('td')[3].innerText;
     const position = el.querySelectorAll('td')[2].innerText;
     const hometown = el.querySelectorAll('td')[5].innerText;
     hs_el = el.querySelectorAll('td')[6];
     const high_school = hs_el ? hs_el.innerText : '';
     ps_el = el.querySelectorAll('td')[7];
     const previous_school = ps_el ? ps_el.innerText : '';
     const jersey = el.querySelectorAll('td')[0].innerText;
     const url = el.querySelectorAll('td')[1].querySelector('a')['href']
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/season/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_byu(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('div.roster-players__group table tbody tr'), el => {
     const id = '';
     const name = el.querySelector('a').innerText;
     const year = el.querySelectorAll('td')[4].innerText;
     const height = el.querySelectorAll('td')[3].innerText;
     const position = el.querySelectorAll('td')[2].innerText;
     const hometown = el.querySelectorAll('td')[5].innerText;
     const high_school = el.querySelectorAll('td')[6].innerText;
     const previous_school = '';
     const jersey = el.querySelectorAll('td')[0].innerText;
     const url =el.querySelector("a")['href'];
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/season/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_sanjose(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.roster__players .roster-card-item'), el => {
     const id = '';
     const name = el.querySelector('.roster-card-item__title-link').innerText;
     const year = el.querySelectorAll('.roster-player-card-profile-field__value')[1].innerText;
     const height = el.querySelectorAll('.roster-player-card-profile-field__value')[0].innerText;
     const position = el.querySelector('.roster-card-item__position').innerText;
     const hometown = el.querySelector(".roster-player-card-profile-field__value--hometown").innerText;
     hs_el = el.querySelector(".roster-player-card-profile-field__value--school");
     const high_school = hs_el ? hs_el.innerText : '';
     ps_el = el.querySelector(".roster-player-card-profile-field__value--previous_school");
     const previous_school = ps_el ? ps_el.innerText : '';
     const jersey = el.querySelector(".roster-card-item__jersey-number").innerText;
     const url =el.querySelector("a")['href'];
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/season/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_iowa_state(team, season):
    roster = []
    er = tldextract.extract(team['url'])
    url = team['url'] + "/roster/" + season
    session = HTMLSession()
    r = session.get(url)
    r.html.render(timeout=30)
    players = r.html.find('li.sidearm-roster-list-item')
    new_cols = ['number', 'name', ]
    for player in players:
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': None,
            'name': player.find('a', first=True).text,
            'year': player.find('span.sidearm-roster-list-item-year', first=True).text,
            'hometown': player.find('div.sidearm-roster-list-item-hometown', first=True).text,
            'high_school': player.find('span.sidearm-roster-list-item-highschool', first=True).text,
            'previous_school': None,
            'height': player.find('span.sidearm-roster-list-item-height', first=True).text,
            'position': player.find('span.sidearm-roster-list-item-position', first=True).text,
            'jersey': player.find('span')[0].text,
            'url': "https://www."+er.domain+"."+er.suffix+player.find('a', first=True).attrs['href'],
            'season': season
        })
    return roster

def fetch_and_parse_temple(team, season):
    roster = []
    er = tldextract.extract(team['url'])
    url = team['url'] + "/roster/" + season
    session = HTMLSession()
    r = session.get(url)
    r.html.render(timeout=30)
    players = r.html.find('div.s-person-card')
    for player in players:
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': None,
            'name': player.find('a')[1].text,
            'year': player.find('span.s-person-details__bio-stats-item')[1].text,
            'hometown': player.find('span.s-person-card__content__person__location-item')[1].text,
            'high_school': player.find('span.s-person-card__content__person__location-item')[0].text,
            'previous_school': None,
            'height': player.find('span.s-person-details__bio-stats-item')[2].text,
            'position': player.find('span.s-person-details__bio-stats-item')[0].text,
            'jersey': player.find('a', first=True).text,
            'url': "https://www."+er.domain+"."+er.suffix+player.find('a', first=True).attrs['href'],
            'season': season
        })
    return roster

def fetch_and_parse_boise_state(team, season):
    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.s-person-card'), el => {
        const id = '';
        const name = el.querySelector('.s-person-card__header__person-details-personal').innerText;
        const year = '';
        const height = el.querySelectorAll('.s-person-card__content__person-details-bio-stats-item')[1].innerText;
        const position = el.querySelectorAll('.s-person-card__content__person-details-bio-stats-item')[0].innerText;
        const hometown = el.querySelectorAll('.s-person-card__content__person-details-location-item')[0].innerText;
        const high_school = el.querySelectorAll('.s-person-card__content__person-details-location-item')[1].innerText;
        const previous_school = '';
        let j = el.querySelector('.s-stamp__text');
        const jersey = j ? j.innerText : '';
        const url = el.querySelector('a')['href'];
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_wyoming(team, season):
    roster = []
    er = tldextract.extract(team['url'])
    url = team['url'] + "/roster/" + season
    session = HTMLSession()
    r = session.get(url)
    r.html.render(timeout=30)
    players = r.html.find('li.sidearm-roster-list-item')
    for player in players:
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': None,
            'name': player.find('a', first=True).attrs['title'],
            'year': player.find('span.sidearm-roster-list-item-year', first=True).text,
            'hometown': player.find('span.sidearm-roster-list-item-hometown', first=True).text,
            'high_school': player.find('span.sidearm-roster-list-item-highschool', first=True).text if player.find('span.sidearm-roster-list-item-highschool', first=True) else None,
            'previous_school': None,
            'height': player.find('span.sidearm-roster-list-item-height', first=True).text,
            'position': player.find('span.sidearm-roster-list-item-position', first=True).text,
            'jersey': player.find('div.sidearm-roster-list-item-number', first=True).text,
            'url': "https://www."+er.domain+"."+er.suffix+player.find('a', first=True).attrs['href'],
            'season': season
        })
    return roster

def fetch_and_parse_nebraska(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('#players .table--roster tbody tr'), el => {
     const id = '';
     const name = el.querySelectorAll('td')[1].innerText;
     const year = el.querySelectorAll('td')[4].innerText;
     const height = el.querySelectorAll('td')[2].innerText;
     const position = el.querySelectorAll('td')[3].innerText;
     const hometown = el.querySelectorAll('td')[5].innerText;
     const high_school = el.children[6].innerText.split('/')[0];
     let prev_school = el.children[6].innerText.split('/');
     const previous_school = prev_school[1] ? prev_school[1] : '';
     const jersey = el.querySelectorAll('td')[0].innerText;
     const url = el.querySelectorAll('td')[1].querySelector('a')['href']
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/season/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_notre_dame(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('#players-table tbody tr'), el => {
     const id = '';
     const name = el.querySelectorAll('td')[1].innerText;
     const year = el.querySelectorAll('td')[4].innerText;
     const height = el.querySelectorAll('td')[3].innerText;
     const position = el.querySelectorAll('td')[2].innerText;
     const hometown = el.querySelectorAll('td')[6].innerText;
     const high_school = el.children[7].innerText.split('/')[0];
     let prev_school = el.children[7].innerText.split('/');
     const previous_school = prev_school[1] ? prev_school[1] : '';
     const jersey = el.querySelectorAll('td')[0].innerText;
     const url = el.querySelectorAll('td')[1].querySelector('a')['href']
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/season/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_iowa(team, season):
    roster = []
    er = tldextract.extract(team['url'])
    url = team['url'] + "/roster/" + "season/" + season
    r = requests.get(url)
    html = BeautifulSoup(r.text, features="html.parser")
    players = html.find_all('div', class_="rosters__table")[0].find('table').find_all('tr')[1:]
    for player in players:
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': None,
            'name': player.find_all('td')[1].text.strip(),
            'year': player.find_all('td')[4].text,
            'hometown': player.find_all('td')[5].text.strip(),
            'high_school': player.find_all('td')[6].text.strip(),
            'previous_school': None,
            'height': player.find_all('td')[3].text,
            'position': player.find_all('td')[2].text,
            'jersey': player.find_all('td')[0].text,
            'url': player.find('a')['href'],
            'season': season
        })
    return roster

def fetch_and_parse_south_carolina(team, season):
    roster = []
    er = tldextract.extract(team['url'])
    url = team['url'] + "/roster/season/" + season
    driver.get(url)
    driver.find_element(By.LINK_TEXT, 'List').click()
    html = BeautifulSoup(driver.page_source, features="html.parser")
    players = html.find('table', id="players-table").find_all('tr')[1:]
    for player in players:
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': None,
            'name': player.find_all('td')[1].text.strip(),
            'year': player.find_all('td')[4].text,
            'hometown': player.find_all('td')[5].text.strip(),
            'high_school': player.find_all('td')[6].text.strip(),
            'previous_school': player.find_all('td')[8].text.strip(),
            'height': player.find_all('td')[3].text,
            'position': player.find_all('td')[2].text,
            'jersey': player.find_all('td')[0].text,
            'url': player.find('a')['href'],
            'season': season
        })
    return roster

def fetch_and_parse_virginia_tech(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.sidearm-roster-list-item'), el => {
     const id = '';
     const name = el.querySelector('.sidearm-roster-list-item-name').innerText;
     const year = el.querySelector('.sidearm-roster-list-item-year').innerText;
     const height = el.querySelector('.sidearm-roster-list-item-height').innerText;
     const position = el.querySelector('.sidearm-roster-list-item-position').innerText;
     const hometown = el.querySelector('.sidearm-roster-list-item-hometown').innerText;
     const high_school = el.querySelector('.sidearm-roster-list-item-highschool').innerText;
     let prev_school = el.querySelector('.sidearm-roster-list-item-previous-school');
     const previous_school = prev_school ? prev_school.innerText : '';
     const jersey = el.querySelector('.sidearm-roster-list-item-photo-number').innerText;
     const url = el.querySelector('a')['href'];
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_kansas_state(team, season):
    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('li.sidearm-roster-player'), el => {
        const id = el.attributes['data-player-id'].textContent;
        const name = el.querySelector('h3').innerText;
        const year = el.querySelector('.sidearm-roster-player-academic-year').innerText;
        const height = el.querySelector('.sidearm-roster-player-height').innerText;
        const position = el.querySelector('.sidearm-roster-player-position-long-short').innerText.trim();
        const hometown = el.querySelector('.sidearm-roster-player-hometown').innerText;
        hs_el = el.querySelector('.sidearm-roster-player-highschool');
        const high_school = hs_el ? hs_el.innerText : '';
        ps_el = el.querySelector('.sidearm-roster-player-previous-school');
        const previous_school = ps_el ? ps_el.innerText : '';
        const jersey = el.querySelector('.sidearm-roster-player-jersey-number').innerText;
        const url = 'https://www.kstatesports.com' + el.attributes['data-player-url'].textContent;
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_valpo(team, season):
    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelector('table').querySelectorAll('tr:not(:first-child)'), el => {
        const id = '';
        const name = el.querySelectorAll('td')[1].innerText;
        const year = el.querySelectorAll('td')[4].innerText;
        const height = el.querySelectorAll('td')[3].innerText;
        const position = el.querySelectorAll('td')[2].innerText;
        const hometown = el.querySelectorAll('td')[5].innerText;
        const high_school = el.querySelectorAll('td')[6].innerText;
        const previous_school = "";
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelectorAll('td')[1].querySelector('a')['href'];
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season + "/?view=list"
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise


def fetch_and_parse_illinois(team, season):
    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.s-table-body__row'), el => {
        const id = '';
        const name = el.querySelectorAll('td')[2].innerText;
        const year = el.querySelectorAll('td')[4].innerText;
        const height = el.querySelectorAll('td')[2].innerText;
        const position = el.querySelectorAll('td')[3].innerText;
        const hometown = el.querySelectorAll('td')[5].innerText;
        const high_school = el.querySelectorAll('td')[6].innerText;
        const previous_school = el.querySelectorAll('td')[9].innerText;
        const jersey = el.querySelectorAll('td')[1].innerText;
        const url = el.querySelectorAll('td')[2].querySelector('a')['href'];
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise


def fetch_and_parse_kentucky(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('div[itemprop="athlete"]') , el => {
     const id = '';
     const name = el.querySelector('h3').innerText;
     const year = el.querySelector('.roster-item__info').innerText.split('-')[1].split(' ')[2];
     const height = el.querySelector('.roster-item__info').innerText.split('-')[1].split(' ')[1];
     const position = el.querySelector('.roster-item__info').innerText.split('-')[0].trim();
     const hometown = '';
     const high_school = '';
     let prev_school = el.querySelector('.sidearm-roster-list-item-previous-school');
     const previous_school = prev_school ? prev_school.innerText : '';
     const jersey = el.querySelector('.roster-item__number').innerText;
     const url = el.querySelector('a')['href'];
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/season/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_list_item(team, season):
    # baylor
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
        Array.from(document.querySelectorAll('.sidearm-roster-list-item'), el => {
         const id = '';
         const name = el.querySelector('.sidearm-roster-player-name').innerText;
         const year = el.querySelector('.sidearm-roster-list-item-year').innerText;
         const height = el.querySelector('.sidearm-roster-list-item-height').innerText;
         const position = el.querySelector('.sidearm-roster-list-item-position').innerText;
         const hometown = el.querySelector('.sidearm-roster-list-item-hometown').innerText;
         const high_school = el.querySelector('.sidearm-roster-list-item-highschool').innerText;
         let prev_school = el.querySelector('.sidearm-roster-list-item-previous-school');
         const previous_school = prev_school ? prev_school.innerText : '';
         const jersey = el.querySelector('.sidearm-roster-list-item-photo-number').innerText;
         const url = el.querySelector('.sidearm-roster-player-name a')['href'];
         return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
        })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_data_tables(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('#DataTables_Table_0 tbody tr'), el => {
     const id = '';
     const name = el.querySelector(".sidearm-table-player-name").innerText;
     const year = el.querySelector(".roster_class").innerText;
     const height = el.querySelector(".height").innerText;
     const position = el.querySelector(".rp_position_short").innerText;
     const hometown = el.querySelector(".hometownhighschool").innerText.split('/')[0].trim();
     let hs = el.querySelector(".hometownhighschool").innerText.split('/');
     const high_school = hs[1] ? hs[1].trim() : '';
     const previous_school = el.querySelector(".player_previous_school").innerText;
     const jersey = el.querySelector(".roster_jerseynum").innerText;
     const url = el.querySelector(".sidearm-table-player-name a")['href'];
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_miss_state(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('#DataTables_Table_0 tbody tr'), el => {
     const id = '';
     const name = el.querySelector(".sidearm-table-player-name").innerText;
     const year = el.querySelector(".roster_class").innerText;
     const height = el.querySelector(".height").innerText;
     const position = el.querySelector(".rp_position_short").innerText;
     const hometown = el.querySelector(".hometownhighschool").innerText.split('/')[0].trim();
     let hs = el.querySelector(".hometownhighschool").innerText.split('/');
     const high_school = hs[1] ? hs[1].split('(')[0].trim() : '';
     const previous_school = hs[1].split('(')[1] ? hs[1].split('(')[1].trim().replace(')','') : '';
     const jersey = el.querySelector(".roster_jerseynum").innerText;
     const url = el.querySelector(".sidearm-table-player-name a")['href'];
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_arkansas(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('table[data-init="datatable_roster"] tbody tr'), el => {
     const cols = el.querySelectorAll('td');
     const id = '';
     const jersey = cols[0].textContent.trim();
     const nameLink = cols[1].querySelector('a');
     const name = nameLink.textContent.trim();
     const url = nameLink.href;
     const position = cols[2].textContent.trim();
     const year = cols[3].textContent.trim();
     const height = cols[4].textContent.trim();
     const hometown = cols[5].textContent.trim();
     const high_school = cols[6].textContent.trim();
     const previous_school = '';
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/?season=" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise


def fetch_and_parse_tennessee(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.sidearm-roster-table tbody tr'), el => {
     const id = '';
     const name = el.children[1].innerText;
     const year = el.children[2].innerText;
     const height = el.children[4].innerText;
     const position = el.children[3].innerText;
     const hometown = el.children[5].innerText;
     const high_school = el.children[6].innerText.split('/')[0];
     let prev_school = el.children[6].innerText.split('/');
     const previous_school = prev_school[1] ? prev_school[1].innerText : '';
     const jersey = el.children[0].innerText;
     const url = el.children[1].querySelector('a')['href']
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def fetch_and_parse_bradley(team, season):
    name = team['team']
    ncaa_id = team['ncaa_id']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.sidearm-roster-table tbody tr'), el => {
     const id = '';
     const name = el.children[1].innerText;
     const year = el.children[4].innerText;
     const height = el.children[3].innerText;
     const position = el.children[2].innerText;
     const hometown = el.children[5].innerText.split('/')[0].trim();
     const high_school = el.children[5].innerText.split('/')[1].trim();
     const previous_school = '';
     const jersey = el.children[0].innerText;
     const url = el.children[1].querySelector('a')['href']
     return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def parse_roster(team, html, season):
    roster = []
    er = tldextract.extract(team['url'])
    try:
        players = html.find_all('li', {'class': 'sidearm-roster-player'})
    except:
        return None
    for player in players:
        position = None
        if player.find('a')['aria-label'].split(' - ')[0].strip() == 'Addison Jeansonne':
            continue
        if player.find('span', {'class': 'sidearm-roster-player-previous-school'}):
            previous_school = player.find('span', {'class': 'sidearm-roster-player-previous-school'}).text
        else:
            previous_school = None
        if player.find('span', {'class': 'sidearm-roster-player-highschool'}):
            high_school_text = player.find('span', {'class': 'sidearm-roster-player-highschool'}).text.strip()
            high_school = " ".join([x.strip() for x in high_school_text.split(' ') if x != ''])
        else:
            high_school = None
        if player.find('span', {'class': 'sidearm-roster-player-height'}):
            height = player.find('span', {'class': 'sidearm-roster-player-height'}).text
        else:
            height = None
        try:
            hometown = player.find('span', {'class': 'sidearm-roster-player-hometown'}).text.strip()
        except:
            hometown = None
        if player.find('div', {'class': 'sidearm-roster-player-position'}).text.strip() == '':
            position = 'N/A'
        if not position and '"' in player.find('div', {'class': 'sidearm-roster-player-position'}).text.strip():
            position = player.find('div', {'class': 'sidearm-roster-player-position'}).text.strip().split()[0]
        if not position and player.find('div', {'class': 'sidearm-roster-player-position'}).find('span', {'class': 'text-bold'}).find('span', {'class': 'sidearm-roster-player-position-long-short hide-on-small-down'}):
            try:
                position = player.find('div', {'class': 'sidearm-roster-player-position'}).find('span', {'class': 'text-bold'}).find('span', {'class': 'sidearm-roster-player-position-long-short hide-on-small-down'}).text.strip()
            except AttributeError:
                position = None
        if not position and player.find('div', {'class': 'sidearm-roster-player-position'}).find('span', {'class': 'text-bold'}):
            try:
                position = player.find('div', {'class': 'sidearm-roster-player-position'}).find('span', {'class': 'text-bold'}).text.strip()
            except AttributeError:
                position = None
        try:
            jersey = player.find('span', {'class': 'sidearm-roster-player-jersey-number'}).text.strip()
        except:
            jersey = None
        try:
            academic_year = player.find_all('span', {'class': 'sidearm-roster-player-academic-year'})[1].text
        except:
            academic_year = None
        try:
            name = player.find('a')['aria-label'].split(' - ')[0].strip()
        except:
            name = player.find('h3').text.strip()
        if 'Instagram' in name:
            name = player.find('h3').text.strip()
        roster.append({
            'team_id': team['ncaa_id'],
            'team': team['team'],
            'id': player['data-player-id'],
            'name': name,
            'year': academic_year,
            'hometown': hometown,
            'high_school': high_school,
            'previous_school': previous_school,
            'height': height,
            'position': position,
            'jersey': jersey,
            'url': "https://www."+er.domain+"."+er.suffix+player.find('a')['href'],
            'season': season
        })
    return roster

def get_all_rosters(season, teams = []):
    unparsed = []
    skipped = []
    teams_json = json.loads(open('/Users/dpwillis/code/womens-volleyball/teams.json').read())
    if len(teams) > 0:
        teams_json = [t for t in teams_json if t['ncaa_id'] in teams]
    teams_with_urls = [x for x in teams_json if "url" in x]
    with open(f"/Users/dpwillis/code/womens-volleyball/rosters_{season}.csv", 'w') as output_file:
        csv_file = csv.writer(output_file)
        csv_file.writerow(['ncaa_id', 'team', 'player_id', 'name', 'year', 'hometown', 'high_school', 'previous_school', 'height', 'position', 'jersey', 'url', 'season'])
        for team in teams_with_urls:
            try:
                if team['ncaa_id'] == 26107 and season == '2021-22':
                    continue
                if 'roster' in team:
                    continue
                print(team['team'])
                if team['ncaa_id'] in [5, 308, 497, 554]:
                    roster = shotscraper_table(team, season)
                elif team['ncaa_id'] in [9, 71, 83, 96, 99, 156, 173, 180, 191, 234, 249, 257, 301, 306, 367, 387, 392, 400, 404, 418, 428, 441, 490, 521, 522, 559, 574, 603, 635, 664, 671, 676, 688, 690, 700, 719, 749, 758]:
                    roster = shotscraper_card(team, season)
                elif team['ncaa_id'] == 128:
                    roster = shotscraper_ucf(team, season)
                elif team['ncaa_id'] in [51, 248, 731]:
                    roster = shotscraper_list_item(team, season)
                elif team['ncaa_id'] in [175, 316, 487]:
                    roster = shotscraper_roster_player(team, season)
                elif team['ncaa_id'] == 430:
                    roster = shotscraper_miss_state(team, season)
                elif team['ncaa_id'] in [556]:
                    roster = shotscraper_data_tables(team, season)
                elif team['ncaa_id'] in [31]:
                    roster = shotscraper_arkansas(team, season)
                elif team['ncaa_id'] in [706]:
                    roster = shotscraper_table_plain(team, season)
                elif team['ncaa_id'] == 77:
                    if str(season[0:1]):
                        season = f"{str(season)[0:5]}20{str(season[5:7])}"
                        roster = fetch_and_parse_byu(team, season)
                elif team['ncaa_id'] == 630:
                    roster = fetch_and_parse_sanjose(team, season)
                elif team['ncaa_id'] == 66:
                    roster = fetch_and_parse_boise_state(team, season)
                elif team['ncaa_id'] == 415:
                    roster = fetch_and_parse_miami(team, season)
                elif team['ncaa_id'] == 147:
                    roster = fetch_and_parse_clemson(team, season)
                elif team['ncaa_id'] == 311:
                    roster = fetch_and_parse_iowa_state(team, season)
                elif team['ncaa_id'] == 312:
                    roster = fetch_and_parse_iowa(team, season)
                elif team['ncaa_id'] in [326, 449, 569, 762, 8981, 30225]:
                    roster = shotscraper_table_wbkb(team, season)
                elif team['ncaa_id'] == 327:
                    roster = fetch_and_parse_kansas_state(team, season)
                elif team['ncaa_id'] == 334:
                    roster = fetch_and_parse_kentucky(team, season)
                elif team['ncaa_id'] == 388:
                    roster = shotscraper_marshall(team, season)
                elif team['ncaa_id'] == 463:
                    roster = fetch_and_parse_nebraska(team, season)
                elif team['ncaa_id'] == 513:
                    roster = fetch_and_parse_notre_dame(team, season)
                elif team['ncaa_id'] == 528:
                    roster = shotscraper_oregon_state(team, season)
                elif team['ncaa_id'] == 532:
                    roster = shotscraper_wbkb_elms(team, season)
                elif team['ncaa_id'] == 623:
                    roster = shotscraper_wbkb_salve(team, season)
                elif team['ncaa_id'] == 648:
                    roster = fetch_and_parse_south_carolina(team, season)
                elif team['ncaa_id'] == 694:
                    roster = fetch_and_parse_tennessee(team, season)
                elif team['ncaa_id'] == 72:
                    roster = fetch_and_parse_bradley(team, season)
                elif team['ncaa_id'] in [721, 80]:
                    roster = shotscraper_airforce(team, season)
                elif team['ncaa_id'] == 735:
                    roster = fetch_and_parse_valpo(team, season)
                elif team['ncaa_id'] == 736:
                    roster = fetch_and_parse_vandy(team, season)
                elif team['ncaa_id'] == 742:
                    roster = fetch_and_parse_virginia_tech(team, season)
                elif team['ncaa_id'] == 809:
                    roster = shotscraper_table_worc(team, season)
                elif team['ncaa_id'] == 811:
                    roster = fetch_and_parse_wyoming(team, season)
                elif team['ncaa_id'] == 532:
                    continue
                elif 'wvball' in team['url']:
                    html = fetch_wbkb_roster(team['url'], season)
                    roster = None
                    if html:
                        roster = parse_roster_wbkb(team, html, season)
                elif 'w-baskbl' in team['url']:
                    html = fetch_baskbl_roster(team['url'], season)
                    roster = parse_roster_baskbl(team, html, season)
                else:
                    html = fetch_roster(team['url'], season)
                    roster = parse_roster(team, html, season)
                if len(roster) > 0:
                    for player in roster:
                        csv_file.writerow([player['team_id'], player['team'], player['id'], player['name'], player['year'], player['hometown'], player['high_school'], player['previous_school'], player['height'], player['position'], player['jersey'], player['url'], season])
                else:
                    unparsed.append(team['ncaa_id'])
            except:
                print("skipping for now")
#                raise
                skipped.append(team['ncaa_id'])
                continue
    return [unparsed, skipped]

def get_all_rosters_baskbl(season):
    teams_json = json.loads(open('/Users/dpwillis/code/womens-volleyball/teams.json').read())
    teams_with_urls = [x for x in teams_json if "url" in x]
    teams_with_baskbl = [x for x in teams_with_urls if 'w-baskbl' in x['url']]
    with open(f"/Users/dpwillis/code/wbb/ncaa/rosters_{season}_baskbl.csv", 'w') as output_file:
        csv_file = csv.writer(output_file)
        csv_file.writerow(['ncaa_id', 'team', 'player_id', 'name', 'year', 'hometown', 'high_school', 'previous_school', 'height', 'position', 'jersey', 'url', 'season'])
        for team in teams_with_baskbl:
            print(team['team'])
            html = fetch_baskbl_roster(team['url'], season)
            roster = parse_roster_baskbl(team, html, season)
            if roster:
                for player in roster:
                    csv_file.writerow(list(player.values()))

def write_one_team(roster, season):
    with open(f"/Users/dpwillis/code/womens-volleyball/rosters_{season}_adds.csv", 'a') as output_file:
        csv_file = csv.writer(output_file)
        for player in roster:
            csv_file.writerow([player['team_id'], player['team'], player['id'], player['name'], player['year'], player['hometown'], player['high_school'], player['previous_school'], player['height'], player['position'], player['jersey'], player['url'], season])

def shotscraper_airforce(team, season):
    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.s-person-card'), el => {
        const id = '';
        const name = el.querySelector('.s-person-details__personal-single-line').innerText;
        const year = el.querySelectorAll('.s-person-details__bio-stats span')[2].innerText.split('\n')[1].trim();
        const height = el.querySelectorAll('.s-person-details__bio-stats span')[4].innerText.split('\n')[1].trim();
        const position = el.querySelectorAll('.s-person-details__bio-stats span')[0].innerText.split('\n')[1].trim();
        const hometown = el.querySelectorAll('span.s-person-card__content__person__location-item')[0].innerText.replace("Hometown\n","");
        const high_school = el.querySelectorAll('span.s-person-card__content__person__location-item')[1].innerText.split('\n')[1];
        const previous_school = '';
        const jersey = el.querySelector('span.s-stamp__text').innerText;
        const url = el.querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = team['ncaa_id']
            player['team'] = team['team']
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_card(team, season):
    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.s-person-card__content'), el => {
        const id = '';
        const name = el.querySelector('.s-person-details__personal-single-line').innerText;
        const year = el.querySelectorAll('.s-person-details__bio-stats-item')[1].innerText.replace('Academic Year\n ','');
        let ht = el.querySelectorAll('.s-person-details__bio-stats-item')[2];
        const height = ht ? ht.innerText.replace('Height\n ','') : '';
        const position = el.querySelectorAll('.s-person-details__bio-stats-item')[0].innerText.replace('Position\n','');
        const hometown = el.querySelectorAll('.s-person-card__content__person__location-item')[0].innerText.replace('Hometown\n','');
        let hs_el = el.querySelectorAll('.s-person-card__content__person__location-item')[1];
        const high_school = hs_el ? hs_el.innerText.replace('Last School\n','') : '';
        const previous_school = '';
        let j = el.querySelector('.s-stamp__text');
        const jersey = j ? j.innerText : '';
        const url = el.querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_card_image(team, season):
    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.s-person-card__content'), el => {
        const id = '';
        const name = el.querySelector('.s-person-card__header__person-details-personal').innerText;
        const year = el.querySelectorAll('.s-person-details__bio-stats-item')[1].innerText;
        let ht = el.querySelectorAll('.s-person-details__bio-stats-item')[2];
        const height = ht ? ht.innerText : '';
        const position = el.querySelectorAll('.s-person-details__bio-stats-item')[0].innerText;
        const hometown = el.querySelectorAll('.s-person-card__content__person__location-item')[0].innerText;
        let hs_el = el.querySelectorAll('.s-person-card__content__person__location-item')[1];
        const high_school = hs_el ? hs_el.innerText : '';
        const previous_school = '';
        let j = el.querySelector('.s-stamp__text');
        const jersey = j ? j.innerText : '';
        const url = el.querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_marshall(team, season):

    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.s-table-body__row'), el => {
        const id = '';
        const name = el.querySelectorAll('td')[1].innerText;
        const year = el.querySelectorAll('td')[4].innerText;
        const height = el.querySelectorAll('td')[2].innerText;
        const position = el.querySelectorAll('td')[3].innerText;
        const hometown = el.querySelectorAll('td')[5].innerText.split(' / ')[0];
        const high_school = el.querySelectorAll('td')[5].innerText.split(' / ')[1];
        const previous_school = '';
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelectorAll('td')[1].querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_oregon_state(team, season):
    # oregon state

    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.s-table-body__row'), el => {
        const id = '';
        const name = el.querySelectorAll('td')[2].innerText;
        const year = el.querySelectorAll('td')[5].innerText;
        const height = el.querySelectorAll('td')[4].innerText;
        const position = el.querySelectorAll('td')[3].innerText;
        const hometown = el.querySelectorAll('td')[6].innerText;
        const high_school = el.querySelectorAll('td')[7].innerText;
        const previous_school = el.querySelectorAll('td')[8].innerText;
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelector('a')['href'];
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_table(team, season):
    # akron

    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.s-table-body__row'), el => {
        const id = '';
        const name = el.querySelectorAll('td')[1].innerText;
        const year = el.querySelectorAll('td')[2].innerText;
        const height = el.querySelectorAll('td')[3].innerText;
        const position = el.querySelectorAll('td')[4].innerText;
        const hometown = el.querySelectorAll('td')[5].innerText;
        hs_el = el.querySelectorAll('td')[6];
        const high_school = hs_el ? hs_el.innerText : '';
        const previous_school = el.querySelectorAll('td')[7].innerText;
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelectorAll('td')[1].querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_ucf(team, season):
    # akron

    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelector('table tbody').querySelectorAll('tr'), el => {
        const id = '';
        const name = el.querySelectorAll('td')[1].innerText;
        const year = el.querySelectorAll('td')[4].innerText;
        const height = el.querySelectorAll('td')[3].innerText;
        const position = el.querySelectorAll('td')[2].innerText;
        const hometown = el.querySelectorAll('td')[5].innerText;
        hs_el = el.querySelectorAll('td')[6];
        const high_school = hs_el ? hs_el.innerText : '';
        ps_el = el.querySelectorAll('td')[7];
        const previous_school = ps_el ? ps_el.innerText : '';
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelectorAll('td')[1].querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_wbkb_salve(team, season):
    # Salve Regina

    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.roster-data table tbody tr'), el => {
        const id = '';
        const name = el.querySelector('a').innerText.trim();
        const year = el.querySelectorAll('td')[4].innerText;
        const height = el.querySelectorAll('td')[3].innerText;
        const position = el.querySelectorAll('td')[2].innerText;
        const hometown = el.querySelectorAll('td')[5].innerText.split(' / ')[0];
        const high_school = el.querySelectorAll('td')[5].innerText.split(' / ')[1];
        const previous_school = '';
        const jersey = el.querySelectorAll('td')[1].innerText;
        const url = el.querySelector('a')['href'];
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'].replace("index", season) + "/roster"
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_wbkb_elms(team, season):
    # Elms

    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.roster-data table tbody tr'), el => {
        const id = '';
        const name = el.querySelector('a').innerText.trim();
        const year = el.querySelectorAll('td')[2].innerText;
        const height = el.querySelectorAll('td')[3].innerText;
        const position = el.querySelectorAll('td')[1].innerText;
        const hometown = el.querySelectorAll('td')[4].innerText.split(' / ')[0];
        const high_school = el.querySelectorAll('td')[4].innerText.split(' / ')[1];
        const previous_school = '';
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelector('a')['href'];
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'].replace("index", season) + "/roster"
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_table_worc(team, season):
    # Worcester State

    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.roster table tbody tr'), el => {
        const id = '';
        const name = el.querySelector('a').innerText.trim();
        const year = el.querySelectorAll('td')[3].innerText;
        const height = el.querySelectorAll('td')[4].innerText;
        const position = el.querySelectorAll('td')[2].innerText;
        const hometown = el.querySelectorAll('td')[5].innerText.split(' / ')[0];
        const high_school = el.querySelectorAll('td')[5].innerText.split(' / ')[1];
        const previous_school = '';
        const jersey = el.querySelectorAll('td')[1].innerText;
        const url = el.querySelector('a')['href'];
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'].replace("index", season) + "/roster"
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_table_wbkb(team, season):
    # Kalamazoo

    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.roster table tbody tr'), el => {
        const id = '';
        const name = el.querySelector('a').innerText.trim();
        const year = el.querySelectorAll('td')[2].innerText;
        const height = el.querySelectorAll('td')[3].innerText;
        const position = el.querySelectorAll('td')[1].innerText;
        const hometown = el.querySelectorAll('td')[4].innerText.split(' / ')[0];
        const high_school = el.querySelectorAll('td')[4].innerText.split(' / ')[1];
        const previous_school = '';
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelector('a')['href'];
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'].replace("index", season) + "/roster"
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_table_plain(team, season):
    # utsa

    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.roster-players__group tbody tr'), el => {
        const id = '';
        const name = el.querySelectorAll('td')[1].innerText;
        const year = el.querySelectorAll('td')[4].innerText;
        const height = el.querySelectorAll('td')[3].innerText;
        const position = el.querySelectorAll('td')[2].innerText;
        const hometown = el.querySelectorAll('td')[5].innerText;
        hs_el = el.querySelectorAll('td')[6];
        const high_school = hs_el ? hs_el.innerText : '';
        ps_el = el.querySelectorAll('td')[7];
        const previous_school = ps_el ? ps_el.innerText : '';
        const jersey = el.querySelectorAll('td')[0].innerText;
        const url = el.querySelectorAll('td')[1].querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/season/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_roster_player(team, season):
    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.sidearm-roster-player-container'), el => {
        const id = '';
        const name = el.querySelector('h3').innerText;
        const year = el.querySelector('.sidearm-roster-player-academic-year').innerText;
        const height = el.querySelector('.sidearm-roster-player-height').innerText;
        const position = el.querySelector('.sidearm-roster-player-position-long-short').innerText.trim();
        const hometown = el.querySelector('.sidearm-roster-player-hometown').innerText;
        hs_el = el.querySelector('.sidearm-roster-player-highschool');
        const high_school = hs_el ? hs_el.innerText : '';
        ps_el = el.querySelector('.sidearm-roster-player-previous-school');
        const previous_school = ps_el ? ps_el.innerText : '';
        j = el.querySelector('.sidearm-roster-player-jersey-number');
        const jersey = j ? j.innerText : '';
        const url = el.querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

def shotscraper_roster_player2(team, season):
    ncaa_id = team['ncaa_id']
    name = team['team']

    # JavaScript to be executed by shot-scraper
    javascript_code = """
    Array.from(document.querySelectorAll('.sidearm-roster-player-container'), el => {
        const id = '';
        const name = el.querySelector('h3').innerText;
        const year = el.querySelector('.sidearm-roster-player-academic-year').innerText;
        ht_el = el.querySelector('span.sidearm-roster-player-height');
        const height = ht_el ? ht_el.innerText : '';
        const position = el.querySelector('.sidearm-roster-player-position').innerText.split(' ')[0];
        const hometown = el.querySelector('.sidearm-roster-player-hometown').innerText;
        const high_school = el.querySelector('.sidearm-roster-player-highschool').innerText;
        ps_el = el.querySelector('.sidearm-roster-player-previous-school');
        const previous_school = ps_el ? ps_el.innerText : '';
        const jersey = el.querySelector('.sidearm-roster-player-jersey-number').innerText;
        const url = el.querySelector('a')['href']
        return {id, name, year, hometown, high_school, previous_school, height, position, jersey, url};
    })
    """

    roster = []
    url = team['url'] + "/roster/" + season
    # Execute shot-scraper with the given JavaScript
    try:
        result = subprocess.check_output(['shot-scraper', 'javascript', url, javascript_code, "--user-agent", "Firefox"])
        parsed_data = json.loads(result)

        for player in parsed_data:
            player['team_id'] = ncaa_id
            player['team'] = name
            player['season'] = season

        return parsed_data
    except:
        raise

# Example usage: python rosters.py -season 2021-22 -url https://baylorbears.com/sports/womens-basketball/

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='NCAA team information')
    parser.add_argument('-season', action='store', dest='season', help='a string season such as "2020-21"')
    parser.add_argument('-url', action='store', dest='url', help='base url for a team')
    results = parser.parse_args()
    roster_html = fetch_roster(results.url, results.season)
    roster = parse_roster(roster_html, results.season)
    print(roster)
