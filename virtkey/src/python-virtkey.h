/*
 *  Authored by Chris Jones <chris.e.jones@gmail.com>
 *  Modified by marmuta <marmvta@googlemail.com>
 *
 *  Copyright (C) 2006, 2007, 2008 Chris Jones
 *  Copyright (C) 2010 marmuta
 *
 * ------------------------------------------------------------------
 *
 * This file is part of the virtkey library.
 *
 * virtkey is free software: you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public License
 * as published by the Free Software Foundation, either version 3 of
 * the License, or (at your option) any later version.
 *
 * virtkey is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with virtkey.  If not, see
 * <http://www.gnu.org/licenses/>.
 */

#ifndef PYTHONFAKEKEY_H_
#define PYTHONFAKEKEY_H_

#endif /*PYTHONFAKEKEY_H_*/

#include <Python.h>
#include <X11/keysym.h>
#include "ucs2keysym.h"
#include <X11/Xlib.h>
#include <X11/extensions/XTest.h>
#include <X11/XKBlib.h>
#include <X11/extensions/XKBrules.h>
#include <X11/extensions/XKBgeom.h>

#define N_MODIFIER_INDEXES (Mod5MapIndex + 1)

/* Module globals */
static PyObject *virtkey_error = NULL;

/* type */
static PyTypeObject virtkey_Type;

/* object */
typedef struct {
  PyObject_HEAD
  char* displayString;
  Display * display;


  int      min_keycode, max_keycode;
  int      n_keysyms_per_keycode;
  KeySym  *keysyms;
  int      held_keycode;
  int      held_state_flags;
  KeyCode  modifier_table[N_MODIFIER_INDEXES];
  int      shift_mod_index, alt_mod_index, meta_mod_index;
  XkbDescPtr kbd;
} virtkey;



static PyObject * virtkey_layout_get_sections(PyObject * self,PyObject *args);

static PyObject * virtkey_send_unicode(PyObject * self,PyObject *args, Bool press);
static PyObject * virtkey_send_keysym(PyObject * self,PyObject *args, Bool press);
static PyObject * virtkey_send_keycode(PyObject * self,PyObject *args, Bool press);

static PyObject * virtkey_press_keycode(PyObject * self,PyObject *args);
static PyObject * virtkey_release_keycode(PyObject * self,PyObject *args);

static PyObject *
virtkey_get_labels_from_keycode_internal(virtkey *cvirt, long keycode,
                                         const long* mod_masks, int num_masks);
static PyObject *
virtkey_get_labels_from_keycode(PyObject *self, PyObject *args);
static PyObject *
virtkey_get_keysyms_from_keycode(PyObject *self, PyObject *args);

static PyObject * virtkey_press_keysym(PyObject * self,PyObject *args);
static PyObject * virtkey_release_keysym(PyObject * self,PyObject *args);

static PyObject * virtkey_press_unicode(PyObject * self,PyObject *args);
static PyObject * virtkey_release_unicode(PyObject * self,PyObject *args);

static PyObject * virtkey_send(virtkey * cvirt, long out, Bool press);

static PyObject * virtkey_Repr(PyObject * self);

static PyObject * virtkey_new(PyTypeObject *type, PyObject *args,
                              PyObject *kwds);

//static PyObject * virtkey_get_layouts(PyObject * self, PyObject * args);

static PyObject * virtkey_reload_kbd(PyObject * self, PyObject * args);

void change_locked_mods(int mask, Bool lock, virtkey * cvirt);

void getKbd(virtkey * cvirt);
void initvirtkey(void);

