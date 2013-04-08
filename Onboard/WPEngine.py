# -*- coding: utf-8 -*-

# Copyright © 2013, marmuta
#
# This file is part of Onboard.
#
# Onboard is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Onboard is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from __future__ import division, print_function, unicode_literals

from contextlib import contextmanager, closing

import os
import time

import Onboard.pypredict as pypredict

import Onboard.utils as utils

from Onboard.Config import Config
config = Config()

import logging
_logger = logging.getLogger(__name__)


class WPLocalEngine(object):
    """ Low level word predictor, local in-process engine. """

    def __new__(cls, *args, **kwargs):
        """
        Singleton magic.
        """
        if not hasattr(cls, "self"):
            cls.self = object.__new__(cls, *args, **kwargs)
            cls.self.construct()
        return cls.self

    def __init__(self):
        """
        Called multiple times, do not use.
        """
        pass

    def construct(self):
        """
        Singleton constructor, runs only once.
        """
        self._model_cache = ModelCache()
        self._auto_save_timer = AutoSaveTimer(self._model_cache)
        self.models = []
        self.persistent_models = []
        self.auto_learn_models = []
        self.scratch_models = []

    def cleanup(self):
        self._auto_save_timer.stop()
        self._model_cache.save_models()

    def set_models(self, persistent_models, auto_learn_models, scratch_models):
        self.models = persistent_models + scratch_models
        self.persistent_models = persistent_models
        self.auto_learn_models = auto_learn_models
        self.auto_learn_models = auto_learn_models
        self.scratch_models = scratch_models

    def load_models(self):
        """
        Pre-load models set with set_models. If this isn't called,
        language models are lazy-loaded on demand.
        """
        self._model_cache.get_models(self.models)

    def predict(self, context_line, limit = 20,
                case_insensitive = False,
                accent_insensitive = False,
                ignore_capitalized = False,
                ignore_non_capitalized = False):
        """ Find completion/prediction choices. """
        LanguageModel = pypredict.LanguageModel
        options = 0
        if case_insensitive:
            options |= LanguageModel.CASE_INSENSITIVE
        if accent_insensitive:
            options |= LanguageModel.ACCENT_INSENSITIVE
        if ignore_capitalized:
            options |= LanguageModel.IGNORE_CAPITALIZED
        if ignore_non_capitalized:
            options |= LanguageModel.IGNORE_NON_CAPITALIZED

        context = pypredict.tokenize_context(context_line)
        choices = self._get_prediction(self.models, context, limit, options)
        _logger.debug("context=" + repr(context))
        _logger.debug("choices=" + repr(choices[:5]))
        return [x[0] for x in choices]

    def learn_text(self, text, allow_new_words):
        """ Count n-grams and add words to the auto-learn models. """
        if self.auto_learn_models:
            tokens, spans = pypredict.tokenize_text(text)

            # Remove trailing single quote, too many false positives.
            # Do this here, because we still want "it's", etc. to
            # incrementally provide completions.
            for i, token in enumerate(tokens):
                if token.endswith("'"):
                    token = token[:-1]
                    if not token: # shouldn't happen
                        token = "<unk>"
                    tokens[i] = token

            models = self._model_cache.get_models(self.auto_learn_models)
            for model in models:
                model.learn_tokens(tokens, allow_new_words)
            _logger.info("learn_text: tokens=" + repr(tokens[:10]))

            # debug: save all learned text for later parameter optimization
            if config.log_learn:
                fn = os.path.join(config.user_dir, "learned_text.txt")
                with open(fn, "a") as f:
                    f.write(text + "\n")

    def learn_scratch_text(self, text):
        """ Count n-grams and add words to the scratch models. """
        tokens, spans = pypredict.tokenize_text(text)
        models = self._model_cache.get_models(self.scratch_models)
        for model in models:
            #print("scratch learn", model, tokens)
            model.learn_tokens(tokens, True)

    def clear_scratch_models(self):
        """ Count n-grams and add words to the scratch models. """
        models = self._model_cache.get_models(self.scratch_models)
        for model in models:
            model.clear()

    def lookup_text(self, text):
        """
        Split <text> into tokens and lookup the individual tokens in each
        of the given language models. This method is meant to be a basis for
        highlighting (partially) unknown words in a display for recently
        typed text.

        The return value is a tuple of two arrays. First an array of tuples
        (start, end, token), one per token, with start and end index pointing
        into <text> and second a two dimensional array of lookup results.
        There is one lookup result per token and language model. Each lookup
        result is either 0 for no match, 1 for an exact match or -n for
        count n partial (prefix) matches.
        """
        lmids = self.models
        toks, spans = pypredict.tokenize_sentence(text)
        tokens  = [(spans[i][0], spans[i][1], t) for i,t in enumerate(toks)]
        counts = [[0 for lmid in lmids] for t in tokens]
        for i,lmid in enumerate(lmids):
            model = self._model_cache.get_model(lmid)
            if model:
                for j,t in enumerate(tokens):
                    counts[j][i] = model.lookup_word(t[2])

        _logger.debug("lookup_words: tokens=%s counts=%s" % \
                     (repr(tokens), repr(counts)) )

        # counts are 0 for no match, 1 for exact match or -n for partial matches
        return tokens, counts

    def word_exists(self, word):
        """
        Does word exist in any of the non-scratch models?
        """
        exists = False
        lmids = self.persistent_models
        for i,lmid in enumerate(lmids):
            model = self._model_cache.get_model(lmid)
            if model:
                count = model.lookup_word(word)
                if count > 0:
                    exists = True
                    break
        return exists

    def tokenize_text(self, text):
        """
        Let the service find the words in text.
        """
        tokens, spans = pypredict.tokenize_text(text)
        return tokens, spans

    def tokenize_text_pythonic(self, text):
        """
        Let the service find the words in text.
        Return python types instead of dbus.Array/String/... .

        Doctests:
        # whitspace have to be respected in spans
        >>> p = WPLocalEngine()
        >>> p.tokenize_text_pythonic("abc  def")
        (['abc', 'def'], [[0, 3], [5, 8]])
        """
        return self.tokenize_text(text)

    def tokenize_context(self, text):
        """ let the service find the words in text """
        tokens = pypredict.tokenize_context(text)
        return tokens

    def get_model_names(self, _class):
        """ Return the names of the available models. """
        names = self._model_cache.find_available_model_names(_class)
        return names

    def get_last_context_token(self, text):
        """ return the very last (partial) word in text """
        tokens = self.tokenize_context(text[-1024:])
        if len(tokens):
            return tokens[-1]
        else:
            return ""

    def _get_prediction(self, lmdesc, context, limit, options):
        lmids, weights = self._model_cache.parse_lmdesc(lmdesc)
        models = self._model_cache.get_models(lmids)

        for m in models:
            # Kneser-ney perfomes best in entropy and ksr measures, but
            # failed in practice for anything but natural language, e.g.
            # shell commands.
            # -> use the second best available: absolute discounting
            #m.smoothing = "kneser-ney"
            m.smoothing = "abs-disc"

            # setup recency caching
            if hasattr(m, "recency_ratio"):
                # Values found with
                # $ pypredict/optimize caching models/en.lm learned_text.txt
                # based on multilingual text actually typed (--log-learning)
                # with onboard over ~3 months.
                # How valid those settings are under different conditions
                # remains to be seen, but for now this is the best I have.
                m.recency_ratio = 0.811
                m.recency_halflife = 96
                m.recency_smoothing = "jelinek-mercer"
                m.recency_lambdas = [0.404, 0.831, 0.444]

        model = pypredict.overlay(models)
        #model = pypredict.linint(models, weights)
        #model = pypredict.loglinint(models, weights)

        choices = model.predictp(context, limit, options=options)

        return choices


class ModelCache:
    """ Loads and caches language models """

    def __init__(self):
        self._language_models = {}

    def get_models(self, lmids):
        models = []
        for lmid in lmids:
            model = self.get_model(lmid)
            if model:
                models.append(model)
        return models

    def get_model(self, lmid):
        """ get language model from cache or load it from disk"""
        lmid = self.canonicalize_lmid(lmid)
        if lmid in self._language_models:
            model = self._language_models[lmid]
        else:
            model = self.load_model(lmid)
            if model:
                self._language_models[lmid] = model
        return model

    def find_available_model_names(self, _class):
        names = []
        models = self._find_models(_class)
        for model in models:
            name = os.path.basename(model)
            name, ext = os.path.splitext(name)
            names.append(name)
        return names

    @staticmethod
    def _find_models(_class):
        models = []

        if _class == "system":
            path = config.get_system_model_dir()
        else:
            path = config.get_user_model_dir()

        try:
            files = os.listdir(path)
            extension = "lm"
            for filename in files:
                if filename.endswith("." + extension):
                    models.append(os.path.join(path, filename))
        except OSError as e:
            _logger.warning("Failed to find language models in '{}': {} ({})" \
                            .format(path, os.strerror(e.errno), e.errno))
        return models

    @staticmethod
    def parse_lmdesc(lmdesc):
        """
        Extract language model ids and interpolation weights from
        the language model description.
        """
        lmids = []
        weights = []

        for entry in lmdesc:
            fields = entry.split(",")

            lmids.append(fields[0])

            weight = 1.0
            if len(fields) >= 2: # weight is optional
                try:
                    weight = float(fields[1])
                except:
                    pass
            weights.append(weight)

        return lmids, weights

    @staticmethod
    def canonicalize_lmid(lmid):
        """
        Fully qualifies and unifies language model ids.
        Fills in missing fields with default values.
        The result is of the format "type:class:name".
        """
        # default values
        result = ["lm", "system", "en"]
        for i, field in enumerate(lmid.split(":")[:3]):
            result[i] = field
        return ":".join(result)

    def load_model(self, lmid):
        type_, class_, name  = lmid.split(":")

        filename = self.get_filename(lmid)

        if type_ == "lm":
            if   class_ == "system":
                if pypredict.read_order(filename) == 1:
                    model = pypredict.UnigramModel()
                else:
                    model = pypredict.DynamicModel()
            elif class_ == "user":
                model = pypredict.CachedDynamicModel()
            elif class_ == "mem":
                model = pypredict.DynamicModel()
            else:
                _logger.error("Unknown class component '{}' in lmid '{}'" \
                              .format(class_, lmid))
                return None
        else:
            _logger.error("Unknown type component '{}' in lmid '{}'" \
                          .format(type_, lmid))
            return None

        if filename:
            self.do_load_model(model, filename, class_)

        return model

    @staticmethod
    def do_load_model(model, filename, class_):
        _logger.info("Loading language model '{}'.".format(filename))

        if not os.path.exists(filename):
            if class_ == "system":
                _logger.warning("System language model '{}' "
                                "doesn't exist, skipping." \
                                .format(filename))
        else:
            try:
                model.load(filename)
            except IOError as ex:
                if not ex.errno is None: # not n-gram count mismatch
                    errno = ex.errno
                    errstr = os.strerror(errno)
                    _logger.error("Failed to load language model '{}': {} ({})" \
                                .format(filename, errstr, errno))
                else:
                    _logger.error(utils.unicode_str(ex))

                if class_ == "user":
                    _logger.error("Saving word suggestions disabled "
                                  "to prevent further data loss.")

    def save_models(self):
        for lmid, model in list(self._language_models.items()):
            if self.can_save(lmid):
                self.save_model(model, lmid)

    @staticmethod
    def can_save(lmid):
        type_, class_, name  = lmid.split(":")
        return class_ == "user"

    def save_model(self, model, lmid):
        type_, class_, name  = lmid.split(":")
        filename = self.get_filename(lmid)

        if filename and \
           (model.modified or \
            not os.path.exists(filename)):

            if model.load_error:
                _logger.warning("Not saving modified language model '{}' "
                                "due to previous error on load." \
                                .format(filename))
            else:
                _logger.info("Saving language model '{}'".format(filename))
                try:
                    # create the path
                    path = os.path.dirname(filename)
                    if not os.path.exists(path):
                        os.makedirs(path)

                    if 1:
                        # save to temp file
                        basename, ext = os.path.splitext(filename)
                        tempfile = basename + ".tmp"
                        model.save(tempfile)

                        # rename to final file
                        if os.path.exists(filename):
                            os.rename(filename, filename + ".bak")
                        os.rename(tempfile, filename)

                    model.modified = False
                except (IOError, OSError) as e:
                    _logger.warning("Failed to save language model '{}': {} ({})" \
                                    .format(filename, os.strerror(e.errno), e.errno))

    @staticmethod
    def get_filename(lmid):
        type_, class_, name  = lmid.split(":")
        if class_ == "mem":
            filename = ""
        else:
            if class_ == "system":
                path = config.get_system_model_dir()
            else: #if class_ == "user":
                path = config.get_user_model_dir()
            ext = type_
            filename = os.path.join(path, name + "." + ext)

        return filename


class AutoSaveTimer(utils.Timer):
    """ Auto-save modified language models periodically """

    def __init__(self, mode_cache, interval = 10*60):
        self._model_cache = mode_cache
        self._interval = interval  # in seconds
        self._last_save_time = 0
        self.start(5, self._on_timer)

    def _on_timer(self):
        t = time.time()
        if t - self._last_save_time > self._interval:
            self._last_save_time = t
            self._model_cache.save_models()
        return True # run again

