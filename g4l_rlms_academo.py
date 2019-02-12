# -*-*- encoding: utf-8 -*-*-

import os
import re
import ast
import sys
import time
import sys
import urlparse
import json
import datetime
import uuid
import hashlib
import threading
import Queue
import functools
import traceback
import pprint

import requests
from bs4 import BeautifulSoup

from flask import Blueprint, request, url_for
from flask.ext.wtf import TextField, PasswordField, Required, URL, ValidationError

from labmanager.forms import AddForm
from labmanager.rlms import register, Laboratory, CacheDisabler, LabNotFoundError, register_blueprint
from labmanager.rlms.base import BaseRLMS, BaseFormCreator, Capabilities, Versions
from labmanager.rlms.queue import QueueTask, run_tasks

    
def dbg(msg):
    if DEBUG:
        print "[%s]" % time.asctime(), msg
        sys.stdout.flush()

def dbg_lowlevel(msg, scope):
    if DEBUG_LOW_LEVEL:
        print "[%s][%s][%s]" % (time.asctime(), threading.current_thread().name, scope), msg
        sys.stdout.flush()


class AcademoAddForm(AddForm):

    DEFAULT_URL = 'http://www.academo.org/'
    DEFAULT_LOCATION = 'United States'
    DEFAULT_PUBLICLY_AVAILABLE = True
    DEFAULT_PUBLIC_IDENTIFIER = 'academo'
    DEFAULT_AUTOLOAD = True

    def __init__(self, add_or_edit, *args, **kwargs):
        super(AcademoAddForm, self).__init__(*args, **kwargs)
        self.add_or_edit = add_or_edit

    @staticmethod
    def process_configuration(old_configuration, new_configuration):
        return new_configuration


class AcademoFormCreator(BaseFormCreator):

    def get_add_form(self):
        return AcademoAddForm

MIN_TIME = datetime.timedelta(hours=24)

def get_laboratories():
    labs_and_identifiers  = ACADEMO.cache.get('get_laboratories',  min_time = MIN_TIME)
    if labs_and_identifiers:
        labs, identifiers = labs_and_identifiers
        return labs, identifiers

    index = requests.get('https://composer.golabz.eu/academo/demos/').text
    soup = BeautifulSoup(index, 'lxml')


    identifiers = {
        # identifier: {
        #     'name': name,
        #     'link': link,
        #     'languages': ['en', 'fr']
        #     'translations_en': {
        #         'foo': 'bar'
        #     }
        # }
    }

    for anchor_element in soup.find_all("a", class_="thumbnail"):
        name_p = anchor_element.find('p')
        if not name_p:
            continue
        
        name = name_p.text
        href = 'https://composer.golabz.eu/academo' + anchor_element['href']
        identifier = anchor_element['href']

        lab_contents_text = requests.get(href).text
        lab_contents = BeautifulSoup(lab_contents_text, 'lxml')
        translation_files = lab_contents.find_all('meta', { 'name': 'translations' })

        languages = [ tf.get('lang') or 'en' for tf in translation_files ] 
        if not languages:
            languages = ['en']
    
        translations = []
        if translation_files:
            english_translation_files = [ tf['value'] for tf in translation_files if tf.get('value') and tf.get('lang') in (None, 'en') ]
            if english_translation_files:
                try:
                    translations = (requests.get(href + english_translation_files[0]).json() or {}).get('messages') or {}
                except:
                    traceback.print_exc()

        identifiers[identifier] = {
            'name': name,
            'link': href,
            'languages': languages,
            'translations_en': { 
                'translations': { 
                    'en': { row['key']: {'value': row['value']} for row in translations },
                },
                'mails': {}
            }
        }

    labs = []
    for identifier, identifier_data in identifiers.items():
        name = identifier_data['name']
        lab = Laboratory(name=name, laboratory_id=identifier, description=name)
        labs.append(lab)

    ACADEMO.cache['get_laboratories'] = (labs, identifiers)
    return labs, identifiers

FORM_CREATOR = AcademoFormCreator()

CAPABILITIES = [ Capabilities.WIDGET, Capabilities.URL_FINDER, Capabilities.CHECK_URLS, Capabilities.TRANSLATIONS, Capabilities.TRANSLATION_LIST ]

class RLMS(BaseRLMS):

    DEFAULT_HEIGHT = '800'
    DEFAULT_SCALE = 9000

    def __init__(self, configuration, *args, **kwargs):
        self.configuration = json.loads(configuration or '{}')

    def get_version(self):
        return Versions.VERSION_1

    def get_capabilities(self):
        return CAPABILITIES

    def get_laboratories(self, **kwargs):
        labs, identifiers = get_laboratories()
        return labs

    def get_base_urls(self):
        return [ 'https://www.academo.org', 'https://academo.org', 'https://composer.golabz.eu/academo/' ]
    
    def get_translation_list(self, laboratory_id):
        labs, identifiers = get_laboratories()
        for identifier, identifier_data in identifiers.items():
            if identifier == laboratory_id:
                return dict(supported_languages=identifier_data['languages'])

        return dict(supported_languages=[])

    def get_translations(self, laboratory_id):
        labs, identifiers = get_laboratories()
        for identifier, identifier_data in identifiers.items():
            if identifier == laboratory_id:
                return identifier_data['translations_en']

        return { 'translations' : {}, 'mails' : {} }

    def get_lab_by_url(self, url):
        laboratories, identifiers = get_laboratories()

        parsed = urlparse.urlparse(url)
        path = parsed.path

        for lab in laboratories:
            if path.endswith(lab.laboratory_id):
                return lab

        return None

    def get_check_urls(self, laboratory_id):
        laboratories, identifiers = get_laboratories()
        lab_data = identifiers.get(laboratory_id)
        if lab_data:
            return [ lab_data['link'] ]
        return []

    def reserve(self, laboratory_id, username, institution, general_configuration_str, particular_configurations, request_payload, user_properties, *args, **kwargs):
        laboratories, identifiers = get_laboratories()
        if laboratory_id not in identifiers:
            raise LabNotFoundError("Laboratory not found: {}".format(laboratory_id))

        url = identifiers[laboratory_id]['link']
        languages = identifiers[laboratory_id]['languages']

        lang = 'en'
        if 'locale' in kwargs:
            lang = kwargs['locale']
            
            if lang not in languages:
                lang = lang.split('_')[0]
                if lang not in languages:
                    lang = 'en'

        url = url + '?lang=' + lang

        response = {
            'reservation_id' : url,
            'load_url' : url,
        }
        return response


    def load_widget(self, reservation_id, widget_name, **kwargs):
        return {
            'url' : reservation_id
        }

    def list_widgets(self, laboratory_id, **kwargs):
        default_widget = dict( name = 'default', description = 'Default widget' )
        return [ default_widget ]


def populate_cache(rlms):
    rlms.get_laboratories()

ACADEMO = register("Academo", ['1.0'], __name__)
ACADEMO.add_local_periodic_task('Populating cache', populate_cache, hours = 15)

DEBUG = ACADEMO.is_debug() or (os.environ.get('G4L_DEBUG') or '').lower() == 'true' or False
DEBUG_LOW_LEVEL = DEBUG and (os.environ.get('G4L_DEBUG_LOW') or '').lower() == 'true'

if DEBUG:
    print("Debug activated")

if DEBUG_LOW_LEVEL:
    print("Debug low level activated")

sys.stdout.flush()

if __name__ == '__main__':
    rlms = RLMS('{}')
    labs = rlms.get_laboratories()
    for lab in labs:
        print rlms.reserve(lab.laboratory_id, 'nobody', 'nowhere', '{}', [], {}, {})
