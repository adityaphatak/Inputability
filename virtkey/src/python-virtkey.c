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

#include "python-virtkey.h"
#include <gdk/gdkkeys.h>
#include <glib.h>
#include <gdk/gdkx.h>
#include <gdk/gdkkeysyms.h>

#if PY_MAJOR_VERSION >= 3
    #define PyString_FromString PyUnicode_FromString
    #define PyString_FromStringAndSize PyUnicode_FromStringAndSize

    #define PyInt_FromLong PyLong_FromLong
#else
#endif

/* constructor */
static PyObject *
virtkey_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
      //initialize python object and open display.
    virtkey *object = (virtkey*)type->tp_alloc(type, 0);

    if (object == NULL)
        return NULL;
    memset(((char*)object)+sizeof(PyObject), 0,
           sizeof(*object)-sizeof(PyObject));

    object->displayString = getenv ("DISPLAY");
    if (!object->displayString) object->displayString = ":0.0";

    object->display = XOpenDisplay(object->displayString);

    if (!object->display) {
        PyErr_SetString(virtkey_error, "failed initialize display :(");
        return NULL;
    }


      XModifierKeymap *modifiers;
      int              mod_index;
     int              mod_key;
      KeyCode         *kp;

      XDisplayKeycodes(object->display,
        &object->min_keycode, &object->max_keycode);

    object->keysyms = XGetKeyboardMapping(object->display,
                    object->min_keycode,
                    object->max_keycode - object->min_keycode + 1,
                    &object->n_keysyms_per_keycode);

    modifiers = XGetModifierMapping(object->display);

    kp = modifiers->modifiermap;

    for (mod_index = 0; mod_index < 8; mod_index++)
    {
        object->modifier_table[mod_index] = 0;

        for (mod_key = 0; mod_key < modifiers->max_keypermod; mod_key++)
        {
            int keycode = kp[mod_index * modifiers->max_keypermod + mod_key];

            if (keycode != 0)
            {
                object->modifier_table[mod_index] = keycode;
                break;
            }
        }
    }

    for (mod_index = Mod1MapIndex; mod_index <= Mod5MapIndex; mod_index++)
    {
        if (object->modifier_table[mod_index])
        {
            KeySym ks = XKeycodeToKeysym(object->display,
                           object->modifier_table[mod_index], 0);

            /*
            *  Note: ControlMapIndex is already defined by xlib
            *        ShiftMapIndex*/


            switch (ks)
            {
            case XK_Meta_R:
            case XK_Meta_L:
              object->meta_mod_index = mod_index;
              break;

            case XK_Alt_R:
            case XK_Alt_L:
              object->alt_mod_index = mod_index;
              break;

            case XK_Shift_R:
            case XK_Shift_L:
              object->shift_mod_index = mod_index;
              break;
            }
        }
    }

    if (modifiers)
        XFreeModifiermap(modifiers);


    getKbd(object);

    if (PyErr_Occurred()){
        Py_DECREF(object);   /* cleanup, call dealloc */
        return NULL;
    }

    return (PyObject *)object;

}

static void
virtkey_dealloc(PyObject * self){
    virtkey * cvirt  = (virtkey *)self;
    if (cvirt->kbd)
        XkbFreeKeyboard (cvirt->kbd, XkbAllComponentsMask, True);
    if (cvirt->keysyms)
        XFree(cvirt->keysyms);
    if (cvirt->display)
        XCloseDisplay(cvirt->display);
    Py_TYPE(self)->tp_free((PyObject*)self);
}

//Need to handle errors here properly.
void getKbd(virtkey * cvirt){
    if (cvirt->kbd)
        XkbFreeKeyboard (cvirt->kbd, XkbAllComponentsMask, True);

    cvirt->kbd = XkbGetKeyboard (cvirt->display,
                                    XkbCompatMapMask |
                                    XkbNamesMask |
                                    XkbGeometryMask,
                                    XkbUseCoreKbd);
    #if 0
    /* test missing keyboard (LP: 526791)
       keyboard on/off every 10 seconds */
    if (getenv ("VIRTKEY_DEBUG"))
    {
        if (cvirt->kbd && time(NULL) % 20 < 10)
        {
            XkbFreeKeyboard (cvirt->kbd, XkbAllComponentsMask, True);
            cvirt->kbd = NULL;
        }
    }
    #endif

    if (cvirt->kbd == NULL){
        PyErr_SetString(virtkey_error,
                        "XkbGetKeyboard failed to get keyboard from x server");
        return;
    }

    /*
    Status stat = XkbGetGeometry (cvirt->display, cvirt->kbd);

    if (stat == BadValue)
        PyErr_SetString(virtkey_error,
            "Error getting keyboard geometry info, Bad Value.\n");
    if (stat == BadImplementation)
        PyErr_SetString(virtkey_error,
            "Error getting keyboard geometry info, Bad Implementation.\n");
    if (stat == BadName)
        PyErr_SetString(virtkey_error,
            "Error getting keyboard geometry info, Bad Name.\n");
    if (stat == BadAlloc)
        PyErr_SetString(virtkey_error,
            "Error getting keyboard geometry info, Bad Alloc.\n");
    */

    if (XkbGetNames (cvirt->display, XkbAllNamesMask, cvirt->kbd) != Success)
        PyErr_SetString(virtkey_error, "Error getting key name info.\n");

    return;
}

long keysym2keycode(virtkey * cvirt, KeySym keysym, int * flags){
    static int modifiedkey;
    KeyCode    code = 0;

    if ((code = XKeysymToKeycode(cvirt->display, keysym)) != 0)
    {
        if (XKeycodeToKeysym(cvirt->display, code, 0) != keysym)
        {

            if (XKeycodeToKeysym(cvirt->display, code, 1) == keysym)
                *flags |= 1;     //can get at it via shift
            else
                code = 0; // urg, some other modifier do it the heavy way
        }
    }


    if (!code)
    {
        int index;

        // Change one of the last 10 keysyms to our converted utf8,
        // remapping the x keyboard on the fly.
        //
        // This make assumption the last 10 arn't already used.
        // TODO: probably safer to check for this.


        modifiedkey = (modifiedkey+1) % 10;

        // Point at the end of keysyms, modifier 0

        index = (cvirt->max_keycode - cvirt->min_keycode - modifiedkey - 1) * cvirt->n_keysyms_per_keycode;

        cvirt->keysyms[index] = keysym;

        XChangeKeyboardMapping(cvirt->display,
                 cvirt->min_keycode,
                 cvirt->n_keysyms_per_keycode,
                 cvirt->keysyms,
                 (cvirt->max_keycode-cvirt->min_keycode));

        XSync(cvirt->display, False);

        // From dasher src;
        // There's no way whatsoever that this could ever possibly
        // be guaranteed to work (ever), but it does.

        code = cvirt->max_keycode - modifiedkey - 1;

        // The below is lightly safer;
        //
        //  code = XKeysymToKeycode(fk->display, keysym);
        //
        // but this appears to break in that the new mapping is not immediatly
        // put to work. It would seem a MappingNotify event is needed so
        // Xlib can do some changes internally ? ( xlib is doing something
        // related to above ? )
        //
        // Probably better to try and grab the mapping notify *here* ?


    }
    return code;
}

/*based on code form libgnomekbd*/
static PyObject *
get_label (KeySym keyval)
{
    gchar buf[5];
    gunichar uc;

    PyObject * label;

    switch (keyval) {
    case GDK_Scroll_Lock:
        label = PyString_FromString("Scroll\nLock");
        break;

    case GDK_space:
        label = PyString_FromString(" ");
        break;

    case GDK_Sys_Req:
        label = PyString_FromString("Sys Rq");
        break;

    case GDK_Page_Up:
        label = PyString_FromString("Page\nUp");
        break;

    case GDK_Page_Down:
        label = PyString_FromString("Page\nDown");
        break;

    case GDK_Num_Lock:
        label = PyString_FromString("Num\nLock");
        break;

    case GDK_KP_Page_Up:
        label = PyString_FromString("Pg Up");
        break;

    case GDK_KP_Page_Down:
        label = PyString_FromString("Pg Dn");
        break;

    case GDK_KP_Home:
        label = PyString_FromString("Home");
        break;

    case GDK_KP_Left:
        label = PyString_FromString("Left");
        break;

    case GDK_KP_End:
        label = PyString_FromString("End");
        break;

    case GDK_KP_Up:
        label = PyString_FromString("Up");
        break;

    case GDK_KP_Begin:
        label = PyString_FromString("Begin");
        break;

    case GDK_KP_Right:
        label = PyString_FromString("Right");
        break;

    case GDK_KP_Enter:
        label = PyString_FromString("Enter");
        break;

    case GDK_KP_Down:
        label = PyString_FromString("Down");
        break;

    case GDK_KP_Insert:
        label = PyString_FromString("Ins");
        break;

    case GDK_KP_Delete:
        label = PyString_FromString("Del");
        break;

    case GDK_dead_grave:
        label = PyString_FromString("ˋ");
        break;

    case GDK_dead_acute:
        label = PyString_FromString("ˊ");
        break;

    case GDK_dead_circumflex:
        label = PyString_FromString("ˆ");
        break;

    case GDK_dead_tilde:
        label = PyString_FromString("~");
        break;

    case GDK_dead_macron:
        label = PyString_FromString("ˉ");
        break;

    case GDK_dead_breve:
        label = PyString_FromString("˘");
        break;

    case GDK_dead_abovedot:
        label = PyString_FromString("˙");
        break;

    case GDK_dead_diaeresis:
        label = PyString_FromString("¨");
        break;

    case GDK_dead_abovering:
        label = PyString_FromString("˚");
        break;

    case GDK_dead_doubleacute:
        label = PyString_FromString("˝");
        break;

    case GDK_dead_caron:
        label = PyString_FromString("ˇ");
        break;

    case GDK_dead_cedilla:
        label = PyString_FromString("¸");
        break;

    case GDK_dead_ogonek:
        label = PyString_FromString("˛");
        break;

        /* case GDK_dead_iota:
         * case GDK_dead_voiced_sound:
         * case GDK_dead_semivoiced_sound: */

    case GDK_dead_belowdot:
        label = PyString_FromString(".");
        break;

    case GDK_horizconnector:
        label = PyString_FromString("horiz\nconn");
        break;

    case GDK_Mode_switch:
        label = PyString_FromString("AltGr");
        break;

    case GDK_Multi_key:
        label = PyString_FromString("Compose");
        break;

    default:
        uc = gdk_keyval_to_unicode (keyval);
        if (uc != 0 && g_unichar_isgraph (uc)) {
            buf[g_unichar_to_utf8 (uc, buf)] = '\0';
            label = PyString_FromString(buf);
        }
        else
        {

            gchar *name = gdk_keyval_name (keyval);

            if (name)
                label = PyString_FromStringAndSize(name,2);
            else
                label = PyString_FromString(" ");

        }
    }
    return label;
}

static PyObject *
virtkey_get_labels_from_keycode_internal(virtkey *cvirt, long keycode,
                                         const long* mod_masks, int num_masks)
{
    static const long mods[] = {0,1,2,128,129};
    int m;
    unsigned int mods_rtn;
    KeySym keysym = 0;

    if (mod_masks == NULL)
    {
        mod_masks = mods;
        num_masks = (sizeof(mods)/sizeof(*mods));
    }
    PyObject* labelTuple = PyTuple_New(num_masks);

    /* get effective group index */
    XkbStateRec state;
    int group = 0;
    if (Success == XkbGetState(cvirt->display, XkbUseCoreKbd, &state) &&
        XkbIsLegalGroup(state.locked_group))  /* be defensive */
        group = state.locked_group;

    for(m = 0; m < num_masks; m++)
    {
        if (XkbTranslateKeyCode (cvirt->kbd, (KeyCode) keycode,
                                 XkbBuildCoreState (mod_masks[m], group),
                                 &mods_rtn, &keysym)){
            PyTuple_SetItem(labelTuple,m,get_label(keysym));
        }
        else
            PyTuple_SetItem(labelTuple,m,PyString_FromString(""));
    }
    return labelTuple;
}

static PyObject *
virtkey_get_keysyms_from_keycode_internal(virtkey *cvirt, long keycode,
                                         const long* mod_masks, int num_masks)
{
    static const long mods[] = {0};
    int m;
    unsigned int mods_rtn;
    KeySym keysym = 0;

    if (mod_masks == NULL)
    {
        mod_masks = mods;
        num_masks = (sizeof(mods)/sizeof(*mods));
    }
    PyObject* tuple = PyTuple_New(num_masks);

    /* get effective group index */
    XkbStateRec state;
    int group = 0;
    if (Success == XkbGetState(cvirt->display, XkbUseCoreKbd, &state) &&
        XkbIsLegalGroup(state.locked_group))  /* be defensive */
        group = state.locked_group;

    for(m = 0; m < num_masks; m++)
    {
        if (!XkbTranslateKeyCode (cvirt->kbd, (KeyCode) keycode,
                                  XkbBuildCoreState (mod_masks[m], group),
                                  &mods_rtn, &keysym)){
            keysym = 0;
        }
        PyTuple_SetItem(tuple, m, PyLong_FromLong(keysym));
    }
    return tuple;
}

static PyObject *
report_key_info (virtkey * cvirt, XkbKeyPtr key, int col, int *x, int *y)
{
    PyObject * keyObject = PyDict_New();

    PyDict_SetItemString(keyObject,"name",
            PyString_FromStringAndSize(key->name.name,XkbKeyNameLength));

    int x1 = 0;
    int x2 = 0;
    int y1 = 0;
    int y2 = 0;



    XkbGeometryPtr geom = cvirt->kbd->geom;
    char name[XkbKeyNameLength+1];
    //XkbKeyAliasPtr aliases = cvirt->kbd->geom->key_aliases;
    int k = cvirt->kbd->max_key_code  - cvirt->kbd->min_key_code;


    // Above calculation is
    // WORKAROUND FOR BUG in XFree86's XKB implementation,
    // which reports kbd->names->num_keys == 0!
    // In fact, num_keys should be max_key_code-1, and the names->keys
    // array is indeed valid!
    //
    // [bug identified in XFree86 4.2.0]


    strncpy (name, key->name.name, XkbKeyNameLength);
    name[XkbKeyNameLength] = '\0';
    *x += key->gap/10;


    PyObject * labelTuple = PyTuple_New(5);//del

    for (k = cvirt->kbd->min_key_code; k < cvirt->kbd->max_key_code; ++k)
    {
        if (!strncmp (name, cvirt->kbd->names->keys[k].name, XkbKeyNameLength))
        {

            labelTuple = virtkey_get_labels_from_keycode_internal(cvirt, k,
                                                                  NULL, 0);


            x1 = *x + geom->shapes[key->shape_ndx].bounds.x1/10;
            y1 = *y + geom->shapes[key->shape_ndx].bounds.y1/10;
            x2 = geom->shapes[key->shape_ndx].bounds.x2/10 -
                                geom->shapes[key->shape_ndx].bounds.x1/10;

            y2 = geom->shapes[key->shape_ndx].bounds.y2/10 -
                                geom->shapes[key->shape_ndx].bounds.y1/10;

            *x += geom->shapes[key->shape_ndx].bounds.x2/10;

            break;
        }
    }

    PyObject * x1ob = PyInt_FromLong(x1);
    PyObject * y1ob = PyInt_FromLong(y1);
    PyObject * x2ob = PyInt_FromLong(x2);
    PyObject * y2ob = PyInt_FromLong(y2);


    PyObject * shape = PyTuple_Pack(4, x1ob, y1ob, x2ob, y2ob);

    Py_DECREF(x1ob);
    Py_DECREF(y1ob);
    Py_DECREF(x2ob);
    Py_DECREF(y2ob);

    PyDict_SetItemString(keyObject,"shape",shape);
    Py_DECREF(shape);

    PyObject * keycodeOb = PyInt_FromLong(k);
    PyDict_SetItemString(keyObject, "keycode", keycodeOb);
    Py_DECREF(keycodeOb);

    /*
    PyObject * keyOb = PyInt_FromLong(keysym);
    PyDict_SetItemString(keyObject,"keysym", keyOb);
    Py_DECREF(keyOb);
    */

    PyDict_SetItemString(keyObject,"labels", labelTuple);

    return keyObject;
}

static PyObject * virtkey_reload_kbd(PyObject * self, PyObject *args)
{
    virtkey * cvirt  = (virtkey *)self;
    getKbd(cvirt);

    if (PyErr_Occurred())
        return NULL;

    Py_INCREF(Py_None);
    return Py_None;
}

static PyObject *
virtkey_layout_get_section_info(PyObject * self,PyObject *args)
{
    char * requestedSection;
    if (PyArg_ParseTuple(args, "s", &requestedSection)){

        XkbGeometryPtr geom;
        virtkey * cvirt  = (virtkey *)self;

        char * sectionString;

        PyObject * returnTuple;

        int i;

        geom = cvirt->kbd->geom;


        for (i = 0; i < geom->num_sections; ++i)
        {
            XkbSectionPtr section = &geom->sections[i];
            sectionString = XGetAtomName (cvirt->display, section->name);
            if (!strcmp(sectionString,requestedSection))
            {
                PyObject *  width = PyInt_FromLong(section->width/10);
                PyObject *  height = PyInt_FromLong(section->height/10);


                //Increfs width, height
                returnTuple = PyTuple_Pack(2,
                                       width,
                                       height);

                Py_DECREF(width);
                Py_DECREF(height);

                free(sectionString);
                return returnTuple;
            }
            free(sectionString);
        }
    }
    return PyTuple_Pack(2,PyInt_FromLong(0),PyInt_FromLong(0));
}


static PyObject * virtkey_layout_get_keys(PyObject * self,PyObject *args)
{
    char * requestedSection;
    if (PyArg_ParseTuple(args, "s", &requestedSection))
    {
        XkbGeometryPtr geom;

        virtkey * cvirt  = (virtkey *)self;

        int i, row, col;


        geom = cvirt->kbd->geom;

        char * sectionString;

        PyObject * rowTuple;

        for (i = 0; i < geom->num_sections; ++i)
        {
            XkbSectionPtr section = &geom->sections[i];
            sectionString = XGetAtomName (cvirt->display, section->name);
            if (!strcmp(sectionString,requestedSection))
            {
                rowTuple = PyTuple_New(section->num_rows);
                for (row = 0; row < section->num_rows; ++row)
                {
                    XkbRowPtr rowp = &section->rows[row];
                    int x = rowp->left/10, y = rowp->top/10;

                    PyObject * keyTuple = PyTuple_New(rowp->num_keys);

                    for (col = 0; col < rowp->num_keys; ++col)
                    {
                        PyObject * key = report_key_info (cvirt,
                                                &rowp->keys[col], col, &x, &y);
                        PyTuple_SET_ITEM(keyTuple,col,key);
                    }
                    PyTuple_SET_ITEM(rowTuple, row,keyTuple);
                }
                free(sectionString);
                return rowTuple;
            }
            free(sectionString);
        }
    }

    return PyTuple_New(0);
}


static PyObject * virtkey_layout_get_sections(PyObject * self,PyObject *args)
{

    XkbGeometryPtr geom;

    int i;

    virtkey * cvirt  = (virtkey *)self;
    geom = cvirt->kbd->geom;

    char * sectionString;

    PyObject * sectionTuple = PyTuple_New(geom->num_sections);

    for (i = 0; i < geom->num_sections; ++i)
    {
        XkbSectionPtr section = &geom->sections[i];
        sectionString = XGetAtomName (cvirt->display, section->name);
        PyTuple_SetItem(sectionTuple, i, PyString_FromString(sectionString));
        free(sectionString);
    }

    return sectionTuple;
}

static PyObject * virtkey_send(virtkey * cvirt, long out, Bool press){

    if (out != 0)
    {

        XTestFakeKeyEvent(cvirt->display, out, press, CurrentTime);
        XSync(cvirt->display, False);

    }else {
        PyErr_SetString(virtkey_error, "failed to get keycode");
        return NULL;
    }

    Py_INCREF(Py_None);
    return Py_None;
}

static PyObject *
virtkey_get_labels_from_keycode(PyObject *self, PyObject *args)
{
    virtkey *cvirt = (virtkey *)self;
    long keycode;
    PyObject* omod_masks = NULL;
    long* mod_masks = NULL;
    int num_masks = 0;
    int i;
    PyObject* results = NULL;

    if (!(PyArg_ParseTuple(args, "i|O", &keycode, &omod_masks)))
        return NULL;

    if (omod_masks)
    {
        if (!PySequence_Check(omod_masks))
        {
            PyErr_SetString(PyExc_ValueError, "expected sequence type");
        }
        else
        {
            num_masks = PySequence_Length(omod_masks);
            mod_masks = PyMem_Malloc(num_masks * sizeof(*mod_masks));
            for (i = 0; i < num_masks; i++)
            {
                PyObject* item = PySequence_GetItem(omod_masks, i);
                if (!PyLong_Check(item))
                {
                    PyErr_SetString(PyExc_ValueError, "expected integer number");
                    break;
                }
                mod_masks[i] = PyLong_AsLong(item);
            }
        }

    }

    if (!PyErr_Occurred())
        results = virtkey_get_labels_from_keycode_internal(cvirt, keycode,
                                                           mod_masks, num_masks);
    if (mod_masks)
        PyMem_Free(mod_masks);

    return results;

}

static PyObject *
virtkey_get_keysyms_from_keycode(PyObject *self, PyObject *args)
{
    virtkey *cvirt = (virtkey *)self;
    long keycode;
    PyObject* omod_masks = NULL;
    long* mod_masks = NULL;
    int num_masks = 0;
    int i;
    PyObject* results = NULL;

    if (!(PyArg_ParseTuple(args, "i|O", &keycode, &omod_masks)))
        return NULL;

    if (omod_masks)
    {
        if (!PySequence_Check(omod_masks))
        {
            PyErr_SetString(PyExc_ValueError, "expected sequence type");
        }
        else
        {
            num_masks = PySequence_Length(omod_masks);
            mod_masks = PyMem_Malloc(num_masks * sizeof(*mod_masks));
            for (i = 0; i < num_masks; i++)
            {
                PyObject* item = PySequence_GetItem(omod_masks, i);
                if (!PyLong_Check(item))
                {
                    PyErr_SetString(PyExc_ValueError, "expected integer number");
                    break;
                }
                mod_masks[i] = PyLong_AsLong(item);
            }
        }

    }

    if (!PyErr_Occurred())
        results = virtkey_get_keysyms_from_keycode_internal(cvirt, keycode,
                                                          mod_masks, num_masks);
    if (mod_masks)
        PyMem_Free(mod_masks);

    return results;

}

static PyObject * virtkey_send_unicode(PyObject * self,PyObject *args, Bool press){
    virtkey * cvirt  = (virtkey *)self;
    long in = 0;
    long  out = 0;
    int flags = 0;
    if ((PyArg_ParseTuple(args, "i", &in))){//find unicode arg in args tuple.
        out = keysym2keycode(cvirt, ucs2keysym(in), &flags);
    }
    if (flags)
        change_locked_mods(flags,press,cvirt);

    return virtkey_send(cvirt, out, press);
}

static PyObject * virtkey_send_keysym(PyObject * self,PyObject *args, Bool press){
    virtkey * cvirt  = (virtkey *)self;
    long in = 0;
    long  out = 0;
    int flags = 0;
    if ((PyArg_ParseTuple(args, "i", &in))){//find keysym arg in args tuple.

        out = keysym2keycode(cvirt, in, &flags);
    }

    if (flags)
        change_locked_mods(flags,press,cvirt);
    return virtkey_send(cvirt, out, press);
}

static PyObject *
virtkey_release_keycode(PyObject *self, PyObject *args){
    return virtkey_send_keycode(self, args, False);
}

static PyObject *
virtkey_press_keycode(PyObject *self, PyObject *args){
    return virtkey_send_keycode(self, args, True);
}

static PyObject *
virtkey_send_keycode(PyObject *self, PyObject *args, Bool press){
    virtkey * cvirt  = (virtkey *)self;
    long keycode;

    if(!PyArg_ParseTuple(args, "i", &keycode)){
        return NULL;
    }

    return virtkey_send(cvirt, keycode, press);
}

static PyObject * virtkey_latch_mod(PyObject * self,PyObject *args)
{
    int mask = 0;

    virtkey * cvirt  = (virtkey *)self;

    if ((PyArg_ParseTuple(args, "i", &mask))){//find mask arg in args tuple.
        XkbLatchModifiers(cvirt->display, XkbUseCoreKbd, mask, mask);
        XSync(cvirt->display, False); //Otherwise it waits until next keypress
    }
    Py_INCREF(Py_None);
    return Py_None;

}

void change_locked_mods(int mask, Bool lock, virtkey * cvirt){

    if (lock){
        XkbLockModifiers(cvirt->display, XkbUseCoreKbd, mask, mask);
    }
    else{
        XkbLockModifiers(cvirt->display, XkbUseCoreKbd, mask, 0);
    }
    XSync(cvirt->display, False);
}


static PyObject * virtkey_lock_mod(PyObject * self,PyObject *args)
{
    int mask = 0;

    virtkey * cvirt  = (virtkey *)self;

    if ((PyArg_ParseTuple(args, "i", &mask))){//find mask arg in args tuple.
        change_locked_mods(mask, True, cvirt);
    }
    Py_INCREF(Py_None);
    return Py_None;

}

static PyObject * virtkey_unlatch_mod(PyObject * self,PyObject *args)
{
    int mask = 0;

    virtkey * cvirt  = (virtkey *)self;

    if ((PyArg_ParseTuple(args, "i", &mask))){//find mask arg in args tuple.
        XkbLatchModifiers(cvirt->display, XkbUseCoreKbd, mask, 0);
        XSync(cvirt->display, False);
    }
    Py_INCREF(Py_None);
    return Py_None;

}

static PyObject * virtkey_unlock_mod(PyObject * self,PyObject *args)
{
    int mask = 0;

    virtkey * cvirt  = (virtkey *)self;

    if ((PyArg_ParseTuple(args, "i", &mask))){//find mask arg in args tuple.
        change_locked_mods(mask, False, cvirt);
    }
    Py_INCREF(Py_None);
    return Py_None;

}

static PyObject * virtkey_get_layouts(PyObject * self, PyObject *args)
{
    int inout = 20;
    int i=0;

    virtkey * cvirt = (virtkey *)self;

    XkbComponentNamesRec names;
    XkbComponentNamesPtr namesPtr = &names;

    namesPtr->keymap = "*";


    XkbComponentListPtr components = XkbListComponents(cvirt->display,
                                                XkbUseCoreKbd,namesPtr,&inout);

    PyObject *tuple = PyTuple_New(components->num_keymaps);

    for(; i<components->num_keymaps; i++){
        char *name = strdup(components->keymaps[i].name);

        //no error checking version of set_item because tuple is empty.
        PyTuple_SET_ITEM(tuple, i, PyString_FromString(name));
    }

    XkbFreeComponentList(components);

    return tuple;
}

static PyObject * virtkey_press_keysym(PyObject * self,PyObject *args)
{
    return virtkey_send_keysym(self, args, True);
}

static PyObject * virtkey_release_keysym(PyObject * self,PyObject *args)
{
    return virtkey_send_keysym(self, args, False);
}

static PyObject * virtkey_press_unicode(PyObject * self,PyObject *args)
{
    return virtkey_send_unicode(self, args, True);

}

static PyObject * virtkey_release_unicode(PyObject * self,PyObject *args)
{
    return virtkey_send_unicode(self, args, False);

}

static PyObject * virtkey_get_current_group_name(PyObject * self,
                                                 PyObject * noargs)
{
    PyObject * result = NULL;
    virtkey * cvirt  = (virtkey *)self;
    Display * display = cvirt->display;

    /* get current group index */
    XkbStateRec state;
    if (Success != XkbGetState(display, XkbUseCoreKbd, &state))
        PyErr_SetString(virtkey_error, "XkbGetState failed");
    else{
        int group = state.locked_group;
        if (!XkbIsLegalGroup(group))  /* be defensive */
            PyErr_SetString(virtkey_error,
                            "invalid effective group");
        else{

            /* get group name */
            if (!cvirt->kbd->names || !cvirt->kbd->names->groups)
                PyErr_SetString(virtkey_error, "no group names available");
            else{
                Atom atom = cvirt->kbd->names->groups[group];
                if (atom != None){
                    char * group_name = XGetAtomName(display, atom);
                    if (group_name){
                        result = PyString_FromString(group_name);
                        XFree(group_name);
                    }
                }
            }
        }
    }

    if (PyErr_Occurred())
        return NULL;

    if (!result)
        Py_RETURN_NONE;

    return result;
}

static PyObject * virtkey_get_current_group(PyObject * self,
                                            PyObject * noargs)
{
    PyObject * result = NULL;
    virtkey * cvirt  = (virtkey *)self;
    Display * display = cvirt->display;

    /* get current group index */
    XkbStateRec state;
    if (Success != XkbGetState(display, XkbUseCoreKbd, &state))
        PyErr_SetString(virtkey_error, "XkbGetState failed");
    else{
        int group = state.locked_group;
        if (!XkbIsLegalGroup(group))  /* be defensive */
            PyErr_SetString(virtkey_error,
                            "invalid effective group");
        else
        {
            result = PyInt_FromLong(group);
        }
    }

    if (PyErr_Occurred())
        return NULL;

    if (!result)
        Py_RETURN_NONE;

    return result;
}

// Reads the contents of the root window property _XKB_RULES_NAMES.
static PyObject * virtkey_get_rules_names(PyObject *self, PyObject *args)
{
    PyObject* result = NULL;
    virtkey * cvirt  = (virtkey *)self;
    XkbRF_VarDefsRec vd;
    char *tmp = NULL;

    Display * display = cvirt->display;

    if (XkbRF_GetNamesProp(display, &tmp, &vd) && tmp)
    {
        result = PyTuple_New(5);
        PyTuple_SetItem(result, 0, PyUnicode_FromString(tmp ? tmp : ""));
        PyTuple_SetItem(result, 1, PyUnicode_FromString(vd.model ? vd.model : ""));
        PyTuple_SetItem(result, 2, PyUnicode_FromString(vd.layout ? vd.layout : ""));
        PyTuple_SetItem(result, 3, PyUnicode_FromString(vd.variant ? vd.variant : ""));
        PyTuple_SetItem(result, 4, PyUnicode_FromString(vd.options ? vd.options : ""));
    }
    else
    {
        result = PyTuple_New(0);
    }

    return result;
}

/* returns a plus-sign separated string of all keyboard layouts */
static PyObject * virtkey_get_layout_symbols(PyObject * self,
                                             PyObject * noargs)
{
    PyObject * result = NULL;
    virtkey * cvirt  = (virtkey *)self;
    Display * display = cvirt->display;

    if (!cvirt->kbd->names || !cvirt->kbd->names->symbols)
        PyErr_SetString(virtkey_error, "no symbols names available");
    else{
        char * symbols = XGetAtomName(display, cvirt->kbd->names->symbols);
        if (symbols){
            result = PyString_FromString(symbols);
            XFree(symbols);
        }
    }

    if (PyErr_Occurred())
        return NULL;

    if (!result)
        Py_RETURN_NONE;

    return result;
}


/* Method table */
static PyMethodDef virtkey_methods[] = {
  {"press_unicode", virtkey_press_unicode, METH_VARARGS},
  {"release_unicode", virtkey_release_unicode, METH_VARARGS},
  {"press_keysym", virtkey_press_keysym, METH_VARARGS},
  {"release_keysym", virtkey_release_keysym, METH_VARARGS},
  {"press_keycode", virtkey_press_keycode, METH_VARARGS},
  {"release_keycode", virtkey_release_keycode, METH_VARARGS},
  {"latch_mod", virtkey_latch_mod, METH_VARARGS},
  {"lock_mod", virtkey_lock_mod, METH_VARARGS},
  {"unlatch_mod", virtkey_unlatch_mod, METH_VARARGS},
  {"unlock_mod", virtkey_unlock_mod, METH_VARARGS},
  {"layout_get_sections", virtkey_layout_get_sections, METH_VARARGS},
  {"layout_get_keys", virtkey_layout_get_keys, METH_VARARGS},
  {"layout_get_section_size", virtkey_layout_get_section_info, METH_VARARGS},
  {"get_layouts", virtkey_get_layouts, METH_VARARGS},
  {"labels_from_keycode", virtkey_get_labels_from_keycode, METH_VARARGS},
  {"keysyms_from_keycode", virtkey_get_keysyms_from_keycode, METH_VARARGS},
  {"reload", virtkey_reload_kbd, METH_VARARGS},
  {"get_current_group_name", virtkey_get_current_group_name, METH_NOARGS},
  {"get_current_group", virtkey_get_current_group, METH_NOARGS},
  {"get_rules_names", virtkey_get_rules_names, METH_NOARGS},
  {"get_layout_symbols", virtkey_get_layout_symbols, METH_NOARGS},
  {NULL, NULL},
};

/* Callback routines */

static PyObject * virtkey_Repr(PyObject * self)
{
    return PyString_FromString("I am a virtkey object");
}

/* Type definition */
/* remember the forward declaration above, this is the real definition */
static PyTypeObject virtkey_Type = {
    PyVarObject_HEAD_INIT(&PyType_Type, 0)
    "virtkey",                  /*tp_name*/
    sizeof(virtkey),            /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    (destructor)virtkey_dealloc, /*tp_dealloc*/
    0,                          /*tp_print*/
    0,                         /*tp_getattr*/
    0,                          /*tp_setattr*/
    0,                         /*tp_compare*/
    (reprfunc)virtkey_Repr,    /*tp_repr*/
    0,                         /*tp_as_number*/
    0,                         /*tp_as_sequence*/
    0,                         /*tp_as_mapping*/
    0,                         /*tp_hash */
    0,                         /*tp_call*/
    0,                         /*tp_str*/
    0,                         /*tp_getattro*/
    0,                         /*tp_setattro*/
    0,                         /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    "virtkey objects",         /* tp_doc */
    0,                           /* tp_traverse */
    0,                           /* tp_clear */
    0,                           /* tp_richcompare */
    0,                           /* tp_weaklistoffset */
    0,                           /* tp_iter */
    0,                           /* tp_iternext */
    virtkey_methods,             /* tp_methods */
    0,                         /* tp_members */
    0,                         /* tp_getset */
    0,                         /* tp_base */
    0,                         /* tp_dict */
    0,                         /* tp_descr_get */
    0,                         /* tp_descr_set */
    0,                         /* tp_dictoffset */
    0,                         /* tp_init */
    0,                         /* tp_alloc */
    virtkey_new,               /* tp_new */
};

/* Module functions */

static PyMethodDef module_methods[] = {
    {NULL, NULL},
};

/* Module init function */

#if PY_MAJOR_VERSION >= 3
    static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT,
        "virtkey",           /* m_name */
        "virtkey module",    /* m_doc */
        -1,                  /* m_size */
        module_methods,      /* m_methods */
        NULL,                /* m_reload */
        NULL,                /* m_traverse */
        NULL,                /* m_clear */
        NULL,                /* m_free */
    };
#endif

static PyObject *
moduleinit(void)
{
    PyObject *m, *d;

    /* finish type object initialization */
    if (PyType_Ready(&virtkey_Type) < 0)
        return NULL;

    #if PY_MAJOR_VERSION >= 3
        m = PyModule_Create(&moduledef);
    #else
        m = Py_InitModule("virtkey", module_methods);
    #endif

    if (m == NULL)
        return NULL;

    d = PyModule_GetDict(m);

    /* add types here that are allowed to be instantiated from python */
    Py_INCREF(&virtkey_Type);
    PyModule_AddObject(m, "virtkey", (PyObject *)&virtkey_Type);

    /* initialize module variables/constants */

#if PYTHON_API_VERSION >= 1007
    virtkey_error = PyErr_NewException("virtkey.error", NULL, NULL);
#else
    virtkey_error = Py_BuildValue("s", "virtkey.error");
#endif
    PyDict_SetItemString(d, "error", virtkey_error);

    return m;
}

#if PY_MAJOR_VERSION < 3
    PyMODINIT_FUNC
    initvirtkey(void)
    {
        moduleinit();
    }
#else
    PyMODINIT_FUNC
    PyInit_virtkey(void)
    {
        return moduleinit();
    }
#endif

