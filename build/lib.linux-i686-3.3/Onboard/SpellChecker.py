
# -*- coding: utf-8 -*-

from __future__ import division, print_function, unicode_literals

import os
import subprocess
import time

### Logging ###
import logging
_logger = logging.getLogger("SpellChecker")
###############


class SpellChecker(object):
    MAX_QUERY_CACHE_SIZE = 100    # max number of cached queries

    def __init__(self, language_db = None):
        self._language_db = language_db
        self._backend = None
        self._cached_queries = {}

    def set_backend(self, backend):
        """ Switch spell check backend on the fly """
        if backend is None:
            if self._backend:
                self._backend.stop()
            self._backend = None
        else:
            if backend == 0:
                _class = hunspell
            else:
                _class = aspell

            if not self._backend or \
               not type(self._backend) == _class:
                if self._backend:
                    self._backend.stop()
                self._backend = _class()

        self.invalidate_query_cache()

    def set_dict_ids(self, dict_ids):
        success = False
        ids = self._find_matching_dicts(dict_ids)
        if self._backend and \
           not ids == self._backend.get_active_dict_ids():
            self._backend.stop()
            if ids:
                self._backend.start(ids)
                success = True
            else:
                _logger.info("No matching dictionaries for '{backend}' {dicts}" \
                             .format(backend=type(self._backend),
                                     dicts=dict_ids))
        self.invalidate_query_cache()
        return success

    def _find_matching_dicts(self, dict_ids):
        results = []
        for dict_id in dict_ids:
            id = self._find_matching_dict(dict_id)
            if id:
                results.append(id)
        return results

    def _find_matching_dict(self, dict_id):
        """
        Try to match up the given dict_id with the available dictionaries.
        Look for alternatives if there is no direct match.
        """
        available_dict_ids = self.get_supported_dict_ids()

        result = ""
        # try an exact match
        if dict_id in available_dict_ids:
            result = dict_id
        else:
            # try separator "-", common for myspell dicts
            alt_id = dict_id.replace("_", "-")
            if alt_id in available_dict_ids:
                result = alt_id
            elif self._language_db:
                # try the language code alone
                lang_code, country_code = self._language_db.split_lang_id(dict_id)
                if lang_code in available_dict_ids:
                    result = lang_code
                else:
                    # try adding the languages main country
                    lang_id = self._language_db.get_main_lang_id(lang_code)
                    if lang_id and lang_id in available_dict_ids:
                        result = lang_id

        return result

    def find_corrections(self, word, caret_offset):
        """
        Return spelling suggestions for word.
        Multiple result sets may be returned, as the spell
        checkers may return more than one result for certain tokens,
        e.g. before and after hyphens.
        """
        span = None
        suggestions = []
        if self._backend:
            results = self.query_cached(word)
            # hunspell splits words at underscores and then
            # returns results for multiple sub-words.
            # -> find the sub-word at the current caret offset.
            for result in results:
                if result[0][0] > caret_offset:
                    break
                suggestions = result[1]
                span = result[0]

        return span, suggestions

    def find_incorrect_spans(self, word):
        """
        Return misspelled spans (begin- to end-index) inside word.
        Multiple result sets may be returned, as the spell
        checkers may return more than one result for certain tokens,
        e.g. before and after hyphens.
        """
        spans = []
        if self._backend:
            results = self.query_cached(word)
            # hunspell splits words at underscores and then
            # returns results for multiple sub-words.
            # -> find the sub-word at the current caret offset.
            spans = [result[0] for result in results]
        return spans

    def query_cached(self, word):
        """
        Return cached query or ask the backend if necessary.
        """
        query = self._cached_queries.get(word)
        if query is None:
            # limit cache size
            size = len(self._cached_queries)
            if size >= self.MAX_QUERY_CACHE_SIZE:
                new_size = size // 2

                _logger.debug("shrinking query cache from {} to {} entries." \
                              .format(size, new_size))

                # discard the oldest entries
                queries = sorted(self._cached_queries.items(),
                                 key = lambda x: x[1][0])
                self._cached_queries = dict(queries[new_size:])

            # query backend
            results = self.query(word)
            query = [0.0, results]
            self._cached_queries[word] = query

        query[0] = time.time()

        return query[1]

    def query(self, word):
        return self._backend.query(word)

    def invalidate_query_cache(self):
        self._cached_queries = {}

    def get_supported_dict_ids(self):
        return self._backend.get_supported_dict_ids()


class SCBackend(object):
    """ Base class of all spellchecker backends """

    def __init__(self, dict_ids = None):
        self._active_dicts = None
        self._p = None

    def start(self, dict_ids = None):
        self._active_dicts = dict_ids
        _logger.info("starting '{backend}' {dicts}" \
                     .format(backend=type(self), dicts=dict_ids))

    def stop(self):
        if self._p:
            _logger.info("stopping '{backend}'" \
                         .format(backend=type(self)))
            self._p.terminate()
            self._p.wait()
            self._p = None
            self._active_dicts = None

    def query(self, text):
        """
        Query for spelling suggestions.
        Text may contain one or more words. Each word generates its own
        list of suggestions. The spell checker backend decides about
        word boundaries.

        Doctests:
        # one prediction token, two words for the spell checker
        >>> sp = hunspell(["en_US"])
        >>> q = sp.query("conter_trop")
        >>> q  # doctest: +ELLIPSIS
        [[[0, 6, 'conter'], [...
        >>> len(q)
        2

        # unrecognized word returns error span with zero choices (# mark)
        >>> q = sp.query("ἄναρχος")
        >>> q  # doctest:
        [[[0, 7, 'ἄναρχος'], []]]
        >>> len(q)
        1
        """
        results = []

        # Check if the process is still running, it might have
        # exited on start due to an unknown dictinary name.
        if self._p and not self._p.poll() is None:
            self._p = None

        if self._p:

            # unicode?
            if type(text) == type(""):
                line = "^" + text + "\n"
                line = line.encode("UTF-8")
            else: # already UTF-8 byte array
                line = b"^" + text + b"\n"

            self._p.stdin.write(line)
            self._p.stdin.flush()
            while True:
                s = self._p.stdout.readline().decode("UTF-8")
                s = s.strip()
                if not s:
                    break
                if s[:1] == "&":
                    sections = s.split(":")
                    a = sections[0].split()
                    begin = int(a[3]) - 1 # -1 for the prefixed ^
                    end   = begin + len(a[1])
                    span = [begin, end, a[1]] # begin, end, word
                    suggestions = sections[1].strip().split(', ')
                    results.append([span, suggestions])
                if s[:1] == "#":
                    sections = s.split(":")
                    a = sections[0].split()
                    begin = int(a[2]) - 1 # -1 for the prefixed ^
                    end   = begin + len(a[1])
                    span = [begin, end, a[1]] # begin, end, word
                    suggestions = []
                    results.append([span, suggestions])

        return results

    def get_supported_dict_ids(self):
        """
        Return raw supported dictionary ids.
        """
        raise NotImplementedError()

    def get_active_dict_ids(self):
        """
        Return active dictionary ids.
        """
        return self._active_dicts


class hunspell(SCBackend):
    """
    Hunspell backend.

    Doctests:
    # known word
    >>> sp = hunspell(["en_US"])
    >>> sp.query("test")
    []

    # unknown word
    >>> sp = hunspell(["en_US"])
    >>> sp.query("jdaskljasd")  # doctest: +ELLIPSIS
    [[...
    """
    def __init__(self, dict_ids = None):
        SCBackend.__init__(self, dict_ids)
        if dict_ids:
            self.start(dict_ids)

    def start(self, dict_ids = None):
        super(hunspell, self).start(dict_ids)

        args = ["hunspell", "-a", "-i", "UTF-8"]
        if dict_ids:
            args += ["-d", ",".join(dict_ids)]

        try:
            self._p = subprocess.Popen(args,
                                       stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE,
                                       close_fds=True)
            self._p.stdout.readline() # skip header line
        except OSError as e:
            _logger.error(_format("Failed to execute '{}', {}", \
                            " ".join(args), e))
            self._p = None

    def get_supported_dict_ids(self):
        """
        Return raw supported dictionary ids.
        They may not all be valid language ids, e.g. en-GB for myspell dicts.
        """
        dict_ids = []
        args = ["hunspell", "-D"]

        try:
            p = subprocess.Popen(args,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 close_fds=True)
            out, err = p.communicate("") # send something to shut hunspell down

            # scrape the dict_ids from stderr
            in_dicts = False
            for line in err.decode("UTF-8").split("\n"):
                if in_dicts:
                    if not "/" in line:
                        break

                    # extract language id
                    lang_id = os.path.basename(line)
                    if not lang_id.lower().startswith("hyph"):
                        dict_ids.append(lang_id)

                if line.startswith("AVAILABLE DICTIONARIES"): # not translated?
                    in_dicts = True

        except OSError as e:
            _logger.error(_format("Failed to execute '{}', {}", \
                            " ".join(args), e))
        return dict_ids


class aspell(SCBackend):
    """
    Aspell backend.

    Doctests:
    # known word
    >>> sp = aspell(["en_US"])
    >>> sp.query("test")
    []

    # unknown word
    >>> sp = aspell(["en_US"])
    >>> sp.query("jdaskljasd")  # doctest: +ELLIPSIS
    [[...
    """
    def __init__(self, dict_ids = None):
        SCBackend.__init__(self, dict_ids)
        if dict_ids:
            self.start(dict_ids)

    def start(self, dict_ids = None):
        super(aspell, self).start(dict_ids)

        args = ["aspell", "-a"]
        if dict_ids:
            args += ["-l", ",".join(dict_ids)]

        try:
            self._p = subprocess.Popen(args,
                                       stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE,
                                       close_fds=True)
            self._p.stdout.readline() # skip header line
        except OSError as e:
            _logger.error(_format("Failed to execute '{}', {}", \
                            " ".join(args), e))
            self._p = None

    def get_supported_dict_ids(self):
        """
        Return raw supported dictionary ids.
        """
        dict_ids = []
        args = ["aspell", "dump", "dicts"]
        try:
            dict_ids = subprocess.check_output(args) \
                                .decode("UTF-8").split("\n")
        except OSError as e:
            _logger.error(_format("Failed to execute '{}', {}", \
                            " ".join(args), e))
        return [id for id in dict_ids if id]

