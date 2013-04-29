#!/usr/bin/python3
# -*- coding: utf-8 -*-
""" Onboard preferences utility """

from __future__ import division, print_function, unicode_literals

import os
import sys
import copy
import shutil
import subprocess
from xml.parsers.expat import ExpatError
from xml.dom import minidom
import gettext
try:
    import dbus.mainloop.glib
except ImportError:
    pass

from gi.repository import GObject, Pango, Gdk, Gtk

# install translation function _() for all modules
from Onboard.LayoutLoaderSVG import LayoutLoaderSVG
from Onboard.SnippetView     import SnippetView
from Onboard.Appearance      import Theme, ColorScheme
from Onboard.Scanner         import ScanMode, ScanDevice
from Onboard.XInput          import XIDeviceManager, XIEventType, XIEventMask
from Onboard.utils           import show_ask_string_dialog, \
                                    show_confirmation_dialog, \
                                    exists_in_path, \
                                    unicode_str, open_utf8

from virtkey import virtkey

app = "onboard"

### Logging ###
import logging
_logger = logging.getLogger("Settings")
###############

### Config Singleton ###
from Onboard.Config import Config, NumResizeHandles
config = Config()
########################


def LoadUI(filebase):
    builder = Gtk.Builder()
    builder.set_translation_domain(app)
    builder.add_from_file(os.path.join(config.install_dir, filebase+".ui"))
    return builder

def format_list_item(text, issystem):
    if issystem:
        return "<i>{0}</i>".format(text)
    return text


class DialogBuilder(object):
    """
    Utility class for simplified widget setup.
    Has helpers for connecting widgets to ConfigObject properties, i.e.
    indirectly to gsettings keys.
    Mostly borrowed from Gerd Kohlberger's ScannerDialog.
    """

    def __init__(self, builder):
        self._builder = builder

    def wid(self, name):
        widget = self._builder.get_object(name)
        assert(widget)
        return widget

    # push button
    def bind_button(self, name, callback):
        w = self.wid(name)
        w.connect("clicked", callback)

    # spin button
    def bind_spin(self, name, config_object, key):
        w = self.wid(name)
        w.set_value(getattr(config_object, key))
        w.connect("value-changed", self.bind_spin_callback, config_object, key)
        getattr(config_object, key + '_notify_add')(w.set_value)

    def bind_spin_callback(self, widget, config_object, key):
        setattr(config_object, key, widget.get_value())

    # scale
    def bind_scale(self, name, config_object, key, widget_callback = None):
        w = self.wid(name)
        w.set_value(getattr(config_object, key))
        w.connect("value-changed", self.bind_scale_callback,
                          config_object, key, widget_callback)
        getattr(config_object, key + '_notify_add')(w.set_value)

    def bind_scale_callback(self, widget, config_object, key, callback):
        value = widget.get_value()
        setattr(config_object, key, value)
        if callback:
            callback(value)

    # color button
    
    def bind_color(self, name, config_object, key):
        w = self.wid(name)
        
        color_rgba = getattr(config_object, key)
        
        color = Gdk.RGBA()
        color.red = color_rgba[0]
        color.green = color_rgba[1]
        color.blue = color_rgba[2]
        color.alpha = color_rgba[3]
        
        w.set_rgba(color)
        
        w.connect("color-set", self.bind_color_callback, config_object, key)
        getattr(config_object, key + '_notify_add')(lambda x: w.set_rgba(Gdk.RGBA()))

    def bind_color_callback(self, widget, config_object, key):
        color = Gdk.RGBA()
        widget.get_rgba(color)
        
        color_rgba = []
        color_rgba.append(color.red)
        color_rgba.append(color.green)
        color_rgba.append(color.blue)
        color_rgba.append(color.alpha)
        
        setattr(config_object, key, color_rgba)
    
    # radio button
    def bind_radio(self, name, config_object, key, widget_callback = None):
        w = self.wid(name)
        w.set_active(name == getattr(config_object, key))
        w.connect("toggled", self.bind_radio_callback, config_object, key, widget_callback, name)
        getattr(config_object, key + '_notify_add')(lambda x: widget_callback)

    def bind_radio_callback(self, widget, config_object, key, callback, value = None):
        if widget.get_active():
            setattr(config_object, key, value)
        if callback:
            callback(widget, value, config_object, key)
            
    # checkbox
    def bind_check(self, name, config_object, key, widget_callback = None):
        w = self.wid(name)
        w.set_active(getattr(config_object, key))
        if widget_callback:
            w.connect("toggled", widget_callback, config_object, key)
        else:
            w.connect("toggled", self.bind_check_callback, config_object, key)
        getattr(config_object, key + '_notify_add')(w.set_active)

    def bind_check_callback(self, widget, config_object, key):
        setattr(config_object, key, widget.get_active())

    # combobox with id column
    def bind_combobox_id(self, name, config_object, key,
                         config_get_callback = None, config_set_callback = None):
        w = self.wid(name)
        if config_get_callback:
            id = config_get_callback(config_object, key)
        else:
            id = str(getattr(config_object, key))
        w.set_active_id(id)
        w.connect("changed", self.bind_combobox_callback,
                  config_object, key, config_set_callback)
        notify_callback = lambda x : w.set_active_id(str(x))
        getattr(config_object, key + '_notify_add')( \
             lambda x: self.notify_combobox_callback(w, config_object, key,
                                                     config_get_callback))
    def notify_combobox_callback(self, widget,
                                 config_object, key, config_get_callback):
        if config_get_callback:
            id = config_get_callback(config_object, key)
        else:
            id = str(getattr(config_object, key))
        widget.set_active_id(id)

    def bind_combobox_callback(self, widget,
                               config_object, key, config_set_callback):
        if config_set_callback:
            config_set_callback(config_object, key, widget.get_active_id())
        else:
            id = widget.get_active_id()
            assert(not id is None)  # make sure ID-Column is 0
            if not id is None:
                setattr(config_object, key, int(widget.get_active_id()))


class Settings(DialogBuilder):
    def __init__(self,mainwin):
        self.themes = {}       # cache of theme objects

        # Use D-bus main loop by default
        if "dbus" in globals():
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        else:
            _logger.warning("D-Bus bindings unavailable.")

        # finish config initialization
        config.init()

        # init dialog builder
        builder = LoadUI("settings")
        DialogBuilder.__init__(self, builder)

        self.window = builder.get_object("settings_window")
        Gtk.Window.set_default_icon_name("onboard")
        self.window.set_title(_("Onboard Preferences"))

        # General tab
        self.status_icon_toggle = builder.get_object("status_icon_toggle")
        self.status_icon_toggle.set_active(config.show_status_icon)
        config.show_status_icon_notify_add(self.status_icon_toggle.set_active)

        self.start_minimized_toggle = builder.get_object(
            "start_minimized_toggle")
        self.start_minimized_toggle.set_active(config.start_minimized)
        config.start_minimized_notify_add(
            self.start_minimized_toggle.set_active)

        self.icon_palette_toggle = builder.get_object("icon_palette_toggle")
        self.icon_palette_toggle.set_active(config.icp.in_use)
        config.icp.in_use_notify_add(self.icon_palette_toggle.set_active)

        #self.modeless_gksu_toggle = builder.get_object("modeless_gksu_toggle")
        #self.modeless_gksu_toggle.set_active(config.modeless_gksu)
        #config.modeless_gksu_notify_add(self.modeless_gksu_toggle.set_active)

        self.onboard_xembed_toggle = builder.get_object("onboard_xembed_toggle")
        self.onboard_xembed_toggle.set_active(config.onboard_xembed_enabled)
        config.onboard_xembed_enabled_notify_add( \
                self.onboard_xembed_toggle.set_active)

        self.show_tooltips_toggle = builder.get_object("show_tooltips_toggle")
        self.show_tooltips_toggle.set_active(config.show_tooltips)
        config.show_tooltips_notify_add(self.show_tooltips_toggle.set_active)

        self.auto_show_toggle = builder.get_object("auto_show_toggle")
        self.auto_show_toggle.set_active(config.auto_show.enabled)
        config.auto_show.enabled_notify_add(self.auto_show_toggle.set_active)

        # window tab
        self.window_decoration_toggle = \
                              builder.get_object("window_decoration_toggle")
        self.window_decoration_toggle.set_active(config.window.window_decoration)
        config.window.window_decoration_notify_add(lambda x:
                                    [self.window_decoration_toggle.set_active(x),
                                     self.update_window_widgets()])

        self.window_state_sticky_toggle = \
                             builder.get_object("window_state_sticky_toggle")
        self.window_state_sticky_toggle.set_active( \
                                             config.window.window_state_sticky)
        config.window.window_state_sticky_notify_add( \
                                    self.window_state_sticky_toggle.set_active)

        self.force_to_top_toggle = builder.get_object("force_to_top_toggle")
        self.force_to_top_toggle.set_active(config.is_force_to_top())
        config.window.force_to_top_notify_add(lambda x: \
                                       [self.force_to_top_toggle.set_active(x),
                                        self.update_window_widgets()])

        self.keep_aspect_ratio_toggle = builder.get_object(
            "keep_aspect_ratio_toggle")
        self.keep_aspect_ratio_toggle.set_active(config.window.keep_aspect_ratio)
        config.window.keep_aspect_ratio_notify_add(
            self.keep_aspect_ratio_toggle.set_active)

        self.transparent_background_toggle = \
                         builder.get_object("transparent_background_toggle")
        self.transparent_background_toggle.set_active(config.window.transparent_background)
        config.window.transparent_background_notify_add(lambda x:
                            [self.transparent_background_toggle.set_active(x),
                             self.update_window_widgets()])

        self.transparency_spinbutton = builder.get_object("transparency_spinbutton")
        self.transparency_spinbutton.set_value(config.window.transparency)
        config.window.transparency_notify_add(self.transparency_spinbutton.set_value)

        self.background_transparency_spinbutton = \
                           builder.get_object("background_transparency_spinbutton")
        self.background_transparency_spinbutton.set_value(config.window.background_transparency)
        config.window.background_transparency_notify_add(self.background_transparency_spinbutton.set_value)

        self.inactivity_frame = builder.get_object("inactive_behavior_frame")

        self.enable_inactive_transparency_toggle = \
                    builder.get_object("enable_inactive_transparency_toggle")
        self.enable_inactive_transparency_toggle.set_active( \
                                        config.window.enable_inactive_transparency)
        config.window.enable_inactive_transparency_notify_add(lambda x: \
                            [self.enable_inactive_transparency_toggle.set_active(x),
                             self.update_window_widgets()])

        self.inactive_transparency_spinbutton = \
                             builder.get_object("inactive_transparency_spinbutton")
        self.inactive_transparency_spinbutton.set_value(config.window.inactive_transparency)
        config.window.inactive_transparency_notify_add(self.inactive_transparency_spinbutton.set_value)

        self.inactive_transparency_delay_spinbutton = \
                             builder.get_object("inactive_transparency_delay_spinbutton")
        self.inactive_transparency_delay_spinbutton.set_value(config.window.inactive_transparency_delay)
        config.window.inactive_transparency_delay_notify_add(self.inactive_transparency_delay_spinbutton.set_value)

        # Keyboard - first page
        self.bind_check("touch_feedback_enabled_toggle",
                        config.keyboard, "touch_feedback_enabled")
        self.bind_check("audio_feedback_enabled_toggle",
                        config.keyboard, "audio_feedback_enabled")
        self.bind_check("show_secondary_labels_toggle",
                        config.keyboard, "show_secondary_labels")

        # Keyboard - Advanced page
        self.bind_combobox_id("default_key_action_combobox",
                        config.keyboard, "default_key_action")
        self.bind_combobox_id("key_synth_combobox",
                        config.keyboard, "key_synth")

        def get_sticky_key_behavior(config_object, key):
            behaviors = getattr(config_object, key)
            return behaviors.get("all", "")
        def set_sticky_key_behavior(config_object, key, value):
            behaviors = getattr(config_object, key).copy()
            behaviors["all"] = value
            setattr(config_object, key, behaviors)
        self.bind_combobox_id("sticky_key_behavior_combobox",
                        config.keyboard, "sticky_key_behavior",
                        get_sticky_key_behavior, set_sticky_key_behavior)
        self.bind_spin("sticky_key_release_delay_spinbutton",
                            config.keyboard, "sticky_key_release_delay")
        self.bind_combobox_id("touch_input_combobox",
                        config.keyboard, "touch_input")
        self.bind_combobox_id("input_event_source_combobox",
                        config.keyboard, "input_event_source")

        # word suggestions
        self._page_word_suggestions = PageWordSuggestions(builder)

        # window, docking
        self.docking_enabled_toggle = \
                             builder.get_object("docking_enabled_toggle")
        self.docking_box = builder.get_object("docking_box")

        def on_docking_enabled_toggle(widget, config_object, key):
            setattr(config_object, key, widget.get_active())
            self.update_window_widgets()
        self.bind_check("docking_enabled_toggle",
                        config.window, "docking_enabled",
                        widget_callback = on_docking_enabled_toggle)
        self.bind_button("docking_settings_button",
                         lambda widget: DockingDialog().run(self.window))

        self.update_window_widgets()

        # layout view
        self.layout_view = builder.get_object("layout_view")
        self.layout_view.append_column( \
                Gtk.TreeViewColumn(None, Gtk.CellRendererText(), markup=0))

        self.user_layout_root = os.path.join(config.user_dir, "layouts/")
        if not os.path.exists(self.user_layout_root):
            os.makedirs(self.user_layout_root)
            
        # Inpitability layout view -first page          
        self.layout_view1 = builder.get_object("layout_view1")
        self.layout_view1.append_column( \
                Gtk.TreeViewColumn(None, Gtk.CellRendererText(), markup=0))  
                
        self.user_layout_root1 = os.path.join(config.user_dir, "inputability/")
        if not os.path.exists(self.user_layout_root1):
            os.makedirs(self.user_layout_root1)        
     
                                                         
        self.layout_remove_button = \
                             builder.get_object("layout_remove_button")
     
        #Inputability Remove Button
        self.layout_remove_button1 = \
                             builder.get_object("layout_remove_button1")    
     
        self.update_layoutList()
        self.update_layout_widgets()

        # theme view
        self.theme_view = builder.get_object("theme_view")
        self.theme_view.append_column(Gtk.TreeViewColumn(None,
                                                         Gtk.CellRendererText(),
                                                         markup=0))
        self.delete_theme_button = builder.get_object("delete_theme_button")
        self.delete_theme_button
        self.customize_theme_button = \
                                   builder.get_object("customize_theme_button")

        user_theme_root = Theme.user_path()
        if not os.path.exists(user_theme_root):
            os.makedirs(user_theme_root)

        self.update_themeList()
        config.theme_notify_add(self.on_theme_changed)

        self.system_theme_tracking_enabled_toggle = \
                    builder.get_object("system_theme_tracking_enabled_toggle")
        self.system_theme_tracking_enabled_toggle.set_active( \
                                        config.system_theme_tracking_enabled)
        config.system_theme_tracking_enabled_notify_add(lambda x: \
                    [self.system_theme_tracking_enabled_toggle.set_active(x),
                     config.update_theme_from_system_theme()])

        # Snippets
        self.snippet_view = SnippetView()
        builder.get_object("snippet_scrolled_window").add(self.snippet_view)
        
        # Inputability - seconde page
        
        self.bind_spin("activation_flash_interval_spinbutton",
                            config.scanner, "activation_flash_interval") #In
        self.bind_spin("activation_flash_count_spinbutton",
                            config.scanner, "activation_flash_count")    #In                
        
        # If scan popup enable then enable delay spin-button
        
        self.wid("scan_feedback_enabled_toggle") \
                .connect_after("toggled", lambda x:self.popup_delay_update_ui())#In
        self.bind_check("scan_feedback_enabled_toggle",
                        config.scanner, "scan_feedback_enabled")#In
                                 
        self.bind_spin("scanner_unpress_delay_spinbutton",
                            config.scanner, "scanner_popup_unpress_delay") #In 
        self.popup_delay_update_ui()                            
        
        # If change-popup-size checkbox enable then enable height and width spin-button
        
        self.wid("size_change_enabled_toggle") \
                .connect_after("toggled", lambda x:self.size_change_update_ui())#In
        
        self.bind_check("size_change_enabled_toggle",
                        config.scanner, "scan_popup_size_change_enabled")
        self.bind_spin("scan_popup_height_spinbutton",
                            config.scanner, "scan_popup_height")          #In  
        self.bind_spin("scan_popup_width_spinbutton",
                            config.scanner, "scan_popup_width")           #In         
        
        self.size_change_update_ui()
        
        # Color type

        def on_color_type_toggle(radio, data, config_object, key):
            if radio.get_active():
                self.scan_color_update_ui()
        	        	
        self.bind_radio("theme_color", config.scanner, "color_type", widget_callback = on_color_type_toggle)
        self.bind_radio("custom_color", config.scanner, "color_type", widget_callback = on_color_type_toggle)
        
        self.bind_color("scan_colorbutton", config.scanner, "scan_color")
        
        self.scan_color_update_ui()
        
        # Inputability-End
        
        # Universal Access
        scanner_enabled = builder.get_object("scanner_enabled")
        scanner_enabled.set_active(config.scanner.enabled)
        config.scanner.enabled_notify_add(scanner_enabled.set_active)

        self.hide_click_type_window_toggle = \
                builder.get_object("hide_click_type_window_toggle")
        self.hide_click_type_window_toggle.set_active( \
                      config.universal_access.hide_click_type_window)
        config.universal_access.hide_click_type_window_notify_add( \
                      self.hide_click_type_window_toggle.set_active)

        self.enable_click_type_window_on_exit_toggle = \
                builder.get_object("enable_click_type_window_on_exit_toggle")
        self.enable_click_type_window_on_exit_toggle.set_active( \
                      config.universal_access.enable_click_type_window_on_exit)
        config.universal_access.enable_click_type_window_on_exit_notify_add( \
                      self.enable_click_type_window_on_exit_toggle.set_active)

        self.num_resize_handles_combobox = \
                         builder.get_object("num_resize_handles_combobox")
        self.update_num_resize_handles_combobox()
        config.resize_handles_notify_add( \
                            lambda x: self.select_num_resize_handles())

        if config.mousetweaks:
            self.bind_spin("hover_click_delay_spinbutton",
                            config.mousetweaks, "dwell_time")
            self.bind_spin("hover_click_motion_threshold_spinbutton",
                            config.mousetweaks, "dwell_threshold")
                           

        # select last active page
        page = config.current_settings_page
        self.settings_notebook = builder.get_object("settings_notebook")
        self.settings_notebook.set_current_page(page)

        self.pages_view = builder.get_object("pages_view")
        sel = self.pages_view.get_selection()
        if sel:
            sel.select_path(Gtk.TreePath(page))

        self.window.show_all()

        # disable hover click controls if mousetweaks isn't installed
        frame = builder.get_object("hover_click_frame")
        frame.set_sensitive(bool(config.mousetweaks))

        self.window.set_keep_above(not mainwin)

        self.window.connect("destroy", Gtk.main_quit)
        builder.connect_signals(self)

        _logger.info("Entering mainloop of Onboard-settings")
        Gtk.main()
    
    #To Enable or disable color_button grid-box
    def scan_color_update_ui(self): #In
        self.wid("scan_colorbutton").set_sensitive(not (config.scanner.color_type == "theme_color"))
    
    #To Enable or disable height and widht grid-box    
    def size_change_update_ui(self): #In
        self.wid("scan_popup_size_grid"). \
                    set_sensitive(config.scanner.scan_popup_size_change_enabled)
    #To Enable or disable popup delay grid-box    
    def popup_delay_update_ui(self): #In
        self.wid("scan_popup_delay_grid"). \
                    set_sensitive(config.scanner.scan_feedback_enabled)        
    
    def on_pages_view_cursor_changed(self, widget):
        sel = widget.get_selection()
        if sel:
            paths = sel.get_selected_rows()[1]
            if paths:
                page_num = paths[0].get_indices()[0]
                config.current_settings_page = page_num
                self.settings_notebook.set_current_page(page_num)

    def on_settings_notebook_switch_page(self, widget, gpage, page_num):
        config.current_settings_page = page_num

    def on_snippet_add_button_clicked(self, event):
        _logger.info("Snippet add button clicked")
        self.snippet_view.append("","")

    def on_snippet_remove_button_clicked(self, event):
        _logger.info("Snippet remove button clicked")
        self.snippet_view.remove_selected()

    def on_auto_show_toggled(self, widget):
        active = widget.get_active()
        if active and \
           not config.check_gnome_accessibility(self.window):
            active = False
        config.auto_show.enabled = active
        self.update_window_widgets()

    def on_status_icon_toggled(self,widget):
        config.show_status_icon = widget.get_active()
        self.update_window_widgets()

    def on_start_minimized_toggled(self,widget):
        config.start_minimized = widget.get_active()

    def on_icon_palette_toggled(self, widget):
        if not config.is_icon_palette_last_unhide_option():
            config.icp.in_use = widget.get_active()
        self.update_window_widgets()

    def on_modeless_gksu_toggled(self, widget):
        config.modeless_gksu = widget.get_active()

    def on_xembed_onboard_toggled(self, widget):
        config.enable_gss_embedding(widget.get_active())

    def on_show_tooltips_toggled(self, widget):
        config.show_tooltips = widget.get_active()

    def on_window_decoration_toggled(self, widget):
        if not config.is_force_to_top():
            config.window.window_decoration = widget.get_active()
        self.update_window_widgets()

    def on_window_state_sticky_toggled(self, widget):
        if not config.is_force_to_top():
            config.window.window_state_sticky = widget.get_active()

    def on_force_to_top_toggled(self, widget):
        if not config.is_docking_enabled():
            config.window.force_to_top = widget.get_active()
        self.update_window_widgets()

    def on_keep_aspect_ratio_toggled(self,widget):
        config.window.keep_aspect_ratio = widget.get_active()

    def update_window_widgets(self):
        force_to_top =  config.is_force_to_top()
        
        self.icon_palette_toggle.set_sensitive( \
                             not config.is_icon_palette_last_unhide_option())
        active = config.is_icon_palette_in_use()
        if self.icon_palette_toggle.get_active() != active:
            self.icon_palette_toggle.set_active(active)

        self.window_decoration_toggle.set_sensitive(not force_to_top)
        active = config.has_window_decoration()
        if self.window_decoration_toggle.get_active() != active:
            self.window_decoration_toggle.set_active(active)

        self.window_state_sticky_toggle.set_sensitive( not force_to_top)
        active = config.get_sticky_state()
        if self.window_state_sticky_toggle.get_active() != active:
            self.window_state_sticky_toggle.set_active(active)

        self.force_to_top_toggle.set_sensitive(not config.is_docking_enabled())
        active = force_to_top
        if self.force_to_top_toggle.get_active() != active:
            self.force_to_top_toggle.set_active(active)

        self.background_transparency_spinbutton.set_sensitive( \
                                        not config.has_window_decoration())
        self.start_minimized_toggle.set_sensitive(\
                                        not config.auto_show.enabled)

        self.auto_show_toggle.set_active(config.auto_show.enabled)

        self.inactivity_frame.set_sensitive(not config.scanner.enabled)
        active = config.is_inactive_transparency_enabled()
        if self.enable_inactive_transparency_toggle.get_active() != active:
            self.enable_inactive_transparency_toggle.set_active(active)

    def update_all_widgets(self):
        pass

    def on_transparent_background_toggled(self, widget):
        config.window.transparent_background = widget.get_active()
        self.update_window_widgets()

    def on_transparency_changed(self, widget):
        config.window.transparency = widget.get_value()

    def on_background_transparency_spinbutton_changed(self, widget):
        config.window.background_transparency = widget.get_value()
        
    def on_enable_inactive_transparency_toggled(self, widget):
        if not config.scanner.enabled:
            config.window.enable_inactive_transparency = widget.get_active()

    def on_inactive_transparency_changed(self, widget):
        config.window.inactive_transparency = widget.get_value()

    def on_inactive_transparency_delay_changed(self, widget):
        config.window.inactive_transparency_delay = widget.get_value()

    def open_user_layout_dir(self,layoutname):
     
         if layoutname =="layout_view":
            if os.path.exists('/usr/bin/nautilus'):
                os.system(("nautilus --no-desktop %s" %self.user_layout_root))
            elif os.path.exists('/usr/bin/thunar'):
                os.system(("thunar %s" %self.user_layout_root))
            else:
                _logger.warning(_("No file manager to open layout folder"))
        
         else: #For .inputability layout folder  
           
            if os.path.exists('/usr/bin/nautilus'):
                os.system(("nautilus --no-desktop %s" %self.user_layout_root1))
            elif os.path.exists('/usr/bin/thunar'):
                os.system(("thunar %s" %self.user_layout_root1))
            else:
                _logger.warning(_("No file manager to open layout folder")) 

    def on_layout_folder_button_clicked(self, widget):
        self.open_user_layout_dir(layoutname="layout_view") 
    
    def on_layout_folder_button_clicked1(self, widget): #For inputability layout folder event 
           self.open_user_layout_dir(layoutname="layout_view1")                   
                        

    def on_personalise_button_clicked(self, widget):
        new_layout_name = show_ask_string_dialog(
            _("Enter name for personalised layout"), self.window)
        if new_layout_name:
            new_filename = os.path.join(self.user_layout_root, new_layout_name) + \
                           config.LAYOUT_FILE_EXTENSION
            LayoutLoaderSVG.copy_layout(config.layout_filename, new_filename)
            self.update_layoutList()
            self.open_user_layout_dir(layoutname="layout_view")        
            
    def on_personalise_button_clicked1(self, widget): #Inputability Personalise button
        new_layout_name = show_ask_string_dialog(
            _("Enter name for personalised layout"), self.window)
        if new_layout_name:
            new_filename = os.path.join(self.user_layout_root1, new_layout_name) + \
                           config.LAYOUT_FILE_EXTENSION
            LayoutLoaderSVG.copy_layout(config.layout_filename, new_filename)
            self.update_layoutList()
            self.open_user_layout_dir(layoutname="layout_view1")        

    def on_scanner_enabled_toggled(self, widget):
        config.scanner.enabled = widget.get_active()
        self.update_window_widgets()

    def on_scanner_settings_clicked(self, widget):
        ScannerDialog().run(self.window)

    def on_hide_click_type_window_toggled(self, widget):
        config.universal_access.hide_click_type_window = widget.get_active()

    def on_enable_click_type_window_on_exit_toggle(self, widget):
        config.universal_access.enable_click_type_window_on_exit = widget.get_active()

    def on_hover_click_settings_clicked(self, widget):
        filename = "gnome-control-center"
        try:
            Popen([filename, "universal-access"])
        except OSError as e:
            _logger.warning(_format("System settings not found ({}): {}",
                                    filename, unicode_str(e)))

    def update_num_resize_handles_combobox(self):
        self.num_resize_handles_list = Gtk.ListStore(str, int)
        self.num_resize_handles_combobox.set_model(self.num_resize_handles_list)
        cell = Gtk.CellRendererText()
        self.num_resize_handles_combobox.clear()
        self.num_resize_handles_combobox.pack_start(cell, True)
        self.num_resize_handles_combobox.add_attribute(cell, 'markup', 0)

        self.num_resize_handles_choices = [
                           # Frame resize handles: None
                           [_("None"), NumResizeHandles.NONE],
                           # Frame resize handles: Corners only
                           [_("Corners only"), NumResizeHandles.SOME],
                           # Frame resize handles: All
                           [_("All corners and edges"),  NumResizeHandles.ALL]
                           ]

        for name, id in self.num_resize_handles_choices:
            it = self.num_resize_handles_list.append((name, id))

        self.select_num_resize_handles()

    def select_num_resize_handles(self):
        num = config.get_num_resize_handles()
        for row in self.num_resize_handles_list:
            if row[1] == num:
                it = row.model.get_iter(row.path)
                self.num_resize_handles_combobox.set_active_iter(it)
                break

    def on_num_resize_handles_combobox_changed(self, widget):
        value = self.num_resize_handles_list.get_value( \
                        self.num_resize_handles_combobox.get_active_iter(),1)
        config.set_num_resize_handles(value)

    def on_close_button_clicked(self, widget):
        self.window.destroy()
        Gtk.main_quit()

    def update_layoutList(self):
    
        self.layoutList = Gtk.ListStore(str, str)
        self.layoutList1 = Gtk.ListStore(str, str)
        self.layout_view.set_model(self.layoutList)
        self.layout_view1.set_model(self.layoutList1)
        
        self.update_layouts(os.path.join(config.install_dir, "layouts"))
        self.update_layouts(self.user_layout_root)
        
        self.update_layouts(os.path.join(config.install_dir, "layouts/inputability"))
        self.update_layouts(self.user_layout_root1)    
   
    #Calling Orca
    def on_acitvate_orca_clicked(self, event):
     

       subprocess.Popen("orca")   
       self.window.destroy()
       Gtk.main_quit()
    

 
    def cb_selected_layout_changed(self):
        self.update_layouts(self.user_layout_root)

    def on_add_button_clicked(self, event):
        chooser = Gtk.FileChooserDialog(title=_("Add Layout"),
                                        parent=self.window,
                                        action=Gtk.FileChooserAction.OPEN,
                                        buttons=(Gtk.STOCK_CANCEL,
                                                 Gtk.ResponseType.CANCEL,
                                                 Gtk.STOCK_OPEN,
                                                 Gtk.ResponseType.OK))
        filterer = Gtk.FileFilter()
        filterer.add_pattern("*.sok")
        filterer.add_pattern("*" + config.LAYOUT_FILE_EXTENSION)
        filterer.set_name(_("Onboard layout files"))
        chooser.add_filter(filterer)

        filterer = Gtk.FileFilter()
        filterer.add_pattern("*")
        filterer.set_name(_("All files"))  
        chooser.add_filter(filterer)

        response = chooser.run()
                
        if response == Gtk.ResponseType.OK:
            filename = chooser.get_filename()

            f = open_utf8(filename)
            sokdoc = minidom.parse(f).documentElement
            for p in sokdoc.getElementsByTagName("pane"):
             # To check which add button was click( Onboard layout Add or inputabiltiy layout Add)
                if event.get_label()== "gtk-add": 
                    
                    fn = p.attributes['filename'].value
                                
                    shutil.copyfile("%s/%s" % (os.path.dirname(filename), fn),
                                "%s%s" % (self.user_layout_root, fn))
                else:
                    
                     fn = p.attributes['filename'].value
                                
                     shutil.copyfile("%s/%s" % (os.path.dirname(filename), fn),
                                "%s%s" % (self.user_layout_root1, fn))
                                
                       
            if event.get_label()== "gtk-add":                                                             
                
                shutil.copyfile(filename,"%s%s" % (self.user_layout_root,
                                               os.path.basename(filename)))
            else:
                shutil.copyfile(filename,"%s%s" % (self.user_layout_root1,
                                             os.path.basename(filename)))      
              
            self.update_layoutList()
        chooser.destroy()


    def on_layout_remove_button_clicked(self, event):
    
        if  event.get_label()== "gtk-remove": 
            sel = self.layout_view.get_selection()
            if sel:
                filename = self.layoutList.get_value(sel.get_selected()[1], 1)

                LayoutLoaderSVG.remove_layout(filename)

                config.layout_filename = self.layoutList[0][1] \
                                     if len(self.layoutList) else ""
        else: # For inputability Layout remove
            sel = self.layout_view1.get_selection()
            if sel:
                filename = self.layoutList1.get_value(sel.get_selected()[1], 1)

                LayoutLoaderSVG.remove_layout(filename)

                config.layout_filename = self.layoutList1[0][1] \
                                     if len(self.layoutList1) else ""
                                                 
        self.update_layoutList()
        
    def update_layouts(self, path):

        filenames = self.find_layouts(path)
         
        layouts = []
        for filename in filenames:
            file_object = open_utf8(filename)
            try:
                sokdoc = minidom.parse(file_object).documentElement

                value = sokdoc.attributes["id"].value
                if os.access(filename, os.W_OK):
                    layouts.append((value.lower(), value, filename))
                else:
                    layouts.append((value.lower(),
                                   "<i>{0}</i>".format(value),
                                   filename))

            except ExpatError as xxx_todo_changeme:
                (strerror) = xxx_todo_changeme
                print("XML in %s %s" % (filename, strerror))
            except KeyError as xxx_todo_changeme1:
                (strerror) = xxx_todo_changeme1
                print("key %s required in %s" % (strerror,filename))

            file_object.close()

        for key, value, filename in sorted(layouts):
                       
            if path.find("inputability") == -1: 
                
                it = self.layoutList.append((value, filename))
            else:
               
                it = self.layoutList1.append((value, filename))           
            
            if filename == config.layout_filename:
                if path.find("inputability")== -1:
                    sel = self.layout_view.get_selection()
                    if sel:
                        sel.select_iter(it)
                else:
                    sel1 = self.layout_view1.get_selection()
                    if sel1:
                        sel1.select_iter(it) 

    def update_layout_widgets(self):
        filename = self.get_selected_layout_filename(layoutname="layout_view")
        self.layout_remove_button.set_sensitive(not filename is None and \
                                         os.access(filename, os.W_OK))
        filename1 = self.get_selected_layout_filename(layoutname="layout_view1")                                 
        self.layout_remove_button1.set_sensitive(not filename1 is None and \
                                  os.access(filename1, os.W_OK))                                            

 
    def find_layouts(self, path):
        files = os.listdir(path)
        layouts = []
        for filename in files:
            if filename.endswith(".sok") or \
               filename.endswith(config.LAYOUT_FILE_EXTENSION):
                layouts.append(os.path.join(path, filename))
        return layouts

    def on_layout_view_cursor_changed(self, widget):
      
      if widget is self.layout_view :
              
        filename = self.get_selected_layout_filename(layoutname="layout_view")
        if filename:
            config.layout_filename = filename
      else : # For Inputability Layout_view1
      
        filename = self.get_selected_layout_filename(layoutname="layout_view1")
        if filename:
            config.layout_filename = filename
                  
      self.update_layout_widgets()
      

    def get_selected_layout_filename(self,layoutname):
        if layoutname == "layout_view":
            sel = self.layout_view.get_selection()
        
            if sel:
                it = sel.get_selected()[1]
                if it:
                    return self.layoutList.get_value(it,1)
        else: #For inputabiblity layout files
            sel = self.layout_view1.get_selection()
        
            if sel:
                it = sel.get_selected()[1]
                if it:
                    return self.layoutList1.get_value(it,1)
        return None
        

    def on_new_theme_button_clicked(self, widget):
        while True:
            new_name = show_ask_string_dialog(
                _("Enter a name for the new theme:"), self.window)
            if not new_name:
                return

            new_filename = Theme.build_user_filename(new_name)
            if not os.path.exists(new_filename):
                break

            question = _format("This theme file already exists.\n'{filename}'"
                               "\n\nOverwrite it?",
                               filename=new_filename)
            if show_confirmation_dialog(question, self.window):
                break

        theme = self.get_selected_theme()
        if not theme:
            theme = Theme()
        theme.save_as(new_name, new_name)
        config.theme_filename = theme.filename
        self.update_themeList()

    def on_delete_theme_button_clicked(self, widget):
        theme = self.get_selected_theme()
        if theme and not theme.is_system:
            if self.get_hidden_theme(theme):
                question = _("Reset selected theme to Onboard defaults?")
            else:
                question = _("Delete selected theme?")
            reply = show_confirmation_dialog(question, self.window)
            if reply == True:
                # be sure the file hasn't been deleted from outside already
                if os.path.exists(theme.filename):
                    os.remove(theme.filename)

                # Is there a system theme behind the deleted one?
                hidden_theme = self.get_hidden_theme(theme)
                if hidden_theme:
                    config.theme_filename = hidden_theme.filename

                else: # row will disappear
                    # find a neighboring theme to select after deletion
                    near_theme = self.find_neighbor_theme(theme)
                    config.theme_filename = near_theme.filename \
                                            if near_theme else ""

                self.update_themeList()

                # notify gsettings clients
                theme = self.get_selected_theme()
                if theme:
                    theme.apply()

    def find_neighbor_theme(self, theme):
        themes = self.get_sorted_themes()
        for i, tpl in enumerate(themes):
            if theme.basename == tpl[0].basename:
                if i < len(themes)-1:
                    return themes[i+1][0]
                else:
                    return themes[i-1][0]
        return None

    def on_system_theme_tracking_enabled_toggled(self, widget):
        config.system_theme_tracking_enabled = widget.get_active()

    def on_customize_theme_button_clicked(self, widget):
        self.customize_theme()

    def on_theme_view_row_activated(self, treeview, path, view_column):
        self.customize_theme()

    def on_theme_view_cursor_changed(self, widget):
        theme = self.get_selected_theme()
        if theme:
            theme.apply()
            config.theme_filename = theme.filename
        self.update_theme_buttons()

    def get_sorted_themes(self):
        #return sorted(self.themes.values(), key=lambda x: x[0].name)
        is_system = [x for x in list(self.themes.values()) if x[0].is_system or x[1]]
        user = [x for x in list(self.themes.values()) if not (x[0].is_system or x[1])]
        return sorted(is_system, key=lambda x: x[0].name.lower()) + \
               sorted(user, key=lambda x: x[0].name.lower())

    def find_theme_index(self, theme):
        themes = self.get_sorted_themes()
        for i,tpl in enumerate(themes):
            if theme.basename == tpl[0].basename:
                return i
        return -1

    def customize_theme(self):
        theme = self.get_selected_theme()
        if theme:
            system_theme = self.themes[theme.basename][1]

            dialog = ThemeDialog(self, theme)
            modified_theme = dialog.run()

            if modified_theme == system_theme:
                # same as the system theme, so delete the user theme
                _logger.info("Deleting theme '%s'" % theme.filename)
                if os.path.exists(theme.filename):
                    os.remove(theme.filename)

            elif not modified_theme == theme:
                # save as user theme
                modified_theme.save_as(theme.basename, theme.name)
                config.theme_filename = modified_theme.filename
                _logger.info("Saved theme '%s'" % theme.filename)

        self.update_themeList()

    def on_theme_changed(self, theme_filename):
        selected = self.get_selected_theme_filename()
        if selected != theme_filename:
            self.update_themeList()

    def update_themeList(self):
        self.themeList = Gtk.ListStore(str, str)
        self.theme_view.set_model(self.themeList)

        self.themes = Theme.load_merged_themes()

        theme_basename = \
               os.path.splitext(os.path.basename(config.theme_filename))[0]
        it_selection = None
        for theme,hidden_theme in self.get_sorted_themes():
            it = self.themeList.append((
                         format_list_item(theme.name, theme.is_system),
                         theme.filename))
            if theme.basename == theme_basename:
                sel = self.theme_view.get_selection()
                if sel:
                    sel.select_iter(it)
                it_selection = it

        # scroll to selection
        if it_selection:
            path = self.themeList.get_path(it_selection)
            self.theme_view.scroll_to_cell(path)

        self.update_theme_buttons()

    def update_theme_buttons(self):
        theme = self.get_selected_theme()

        if theme and (self.get_hidden_theme(theme) or theme.is_system):
            self.delete_theme_button.set_label(_("Reset"))
        else:
            self.delete_theme_button.set_label(Gtk.STOCK_DELETE)

        self.delete_theme_button.set_sensitive(bool(theme) and not theme.is_system)
        self.customize_theme_button.set_sensitive(bool(theme))

    def get_hidden_theme(self, theme):
        if theme:
            return self.themes[theme.basename][1]
        return None

    def get_selected_theme(self):
        filename = self.get_selected_theme_filename()
        if filename:
            basename = os.path.splitext(os.path.basename(filename))[0]
            if basename in self.themes:
                return self.themes[basename][0]
        return None

    def get_selected_theme_filename(self):
        sel = self.theme_view.get_selection()
        if sel:
            it = sel.get_selected()[1]
            if it:
                return self.themeList.get_value(it, 1)
        return None


class PageWordSuggestions(DialogBuilder):
    """ Word Suggestions """

    def __init__(self, builder):
        DialogBuilder.__init__(self, builder)

        self.wid("enable_word_suggestions_toggle") \
                .connect_after("toggled", lambda x: self._update_ui())
        self.bind_check("enable_word_suggestions_toggle",
                        config.word_suggestions, "enabled")
        self.bind_check("auto_learn_toggle",
                            config.wp, "auto_learn")
        self.bind_check("punctuation_assistence_toggle",
                            config.wp, "punctuation_assistance")
        self.bind_check("auto_capitalization_toggle",
                            config.typing_assistance, "auto_capitalization")
        self.bind_check("auto_correction_toggle",
                            config.typing_assistance, "auto_correction")
        self.bind_check("enable_spell_check_toggle",
                            config.word_suggestions, "spelling_suggestions_enabled")
        #self.bind_check("show_context_line_toggle",
        #                config.word_suggestions, "show_context_line")
        self._init_spell_checker_backend_combo()

        self._update_ui()

    def _init_spell_checker_backend_combo(self):
        combo = self.wid("spell_check_backend_combobox")
        combo.set_active(config.typing_assistance.spell_check_backend)
        combo.connect("changed", self.on_spell_check_backend_changed)
        config.typing_assistance.spell_check_backend_notify_add(self._backend_notify)

    def on_spell_check_backend_changed(self, widget):
        config.typing_assistance.spell_check_backend = widget.get_active()

    def _backend_notify(self, mode):
        self.wid("spell_check_backend_combobox").set_active(mode)

    def _update_ui(self):
        self.wid("word_suggestions_general_box1") \
                .set_sensitive(config.are_word_suggestions_enabled())


class DockingDialog(DialogBuilder):
    """ Dialog "Docking Settings" """

    def __init__(self):

        builder = LoadUI("settings_docking_dialog")

        DialogBuilder.__init__(self, builder)

        self.bind_check("docking_shrink_workarea_toggle",
                        config.window, "docking_shrink_workarea")
        self.bind_check("landscape_dock_expand_toggle",
                        config.window.landscape, "dock_expand")
        self.bind_check("portrait_dock_expand_toggle",
                        config.window.portrait, "dock_expand")
        self.bind_combobox_id("docking_edge_combobox",
                        config.window, "docking_edge")

        self.update_ui()

    def run(self, parent):
        dialog = self.wid("dialog")
        dialog.set_transient_for(parent)
        dialog.run()
        dialog.destroy()

    def update_ui(self):
        pass


class ThemeDialog(DialogBuilder):
    """ Customize theme dialog """

    current_page = 0

    def __init__(self, settings, theme):

        self.original_theme = theme
        self.theme = copy.deepcopy(theme)

        builder = LoadUI("settings_theme_dialog")
        DialogBuilder.__init__(self, builder)

        self.dialog = builder.get_object("customize_theme_dialog")

        self.theme_notebook = builder.get_object("theme_notebook")

        self.key_style_combobox = builder.get_object("key_style_combobox")
        self.color_scheme_combobox = builder.get_object("color_scheme_combobox")
        self.font_combobox = builder.get_object("font_combobox")
        self.font_attributes_view = builder.get_object("font_attributes_view")
        self.background_gradient_scale = builder.get_object(
                                               "background_gradient_scale")
        self.key_roundness_scale = builder.get_object(
                                               "key_roundness_scale")
        self.key_size_scale = builder.get_object(
                                               "key_size_scale")
        self.gradients_box = builder.get_object("gradients_box")
        self.key_fill_gradient_scale = builder.get_object(
                                               "key_fill_gradient_scale")
        self.key_stroke_gradient_scale = builder.get_object(
                                               "key_stroke_gradient_scale")
        self.key_gradient_direction_scale = builder.get_object(
                                               "key_gradient_direction_scale")
        self.key_shadow_strength_scale = builder.get_object(
                                               "key_shadow_strength_scale")
        self.key_shadow_size_scale = builder.get_object(
                                               "key_shadow_size_scale")
        self.revert_button = builder.get_object("revert_button")
        self.superkey_label_combobox = builder.get_object(
                                               "superkey_label_combobox")
        self.superkey_label_size_checkbutton = builder.get_object(
                                            "superkey_label_size_checkbutton")
        self.superkey_label_model = builder.get_object("superkey_label_model")

        def on_key_stroke_width_value_changed(value):
            self.theme.background_gradient = value
            self.update_sensivity()
        self.bind_scale("key_stroke_width_scale",
                        config.theme_settings, "key_stroke_width",
                        widget_callback = on_key_stroke_width_value_changed)

        self.update_ui()

        self.dialog.set_transient_for(settings.window)
        self.theme_notebook.set_current_page(ThemeDialog.current_page)

        builder.connect_signals(self)

    def run(self):
        # do response processing ourselves to stop the
        # revert button from closing the dialog
        self.dialog.set_modal(True)
        self.dialog.show()
        Gtk.main()
        self.dialog.destroy()
        return self.theme

    def on_response(self, dialog, response_id):
        if response_id == Gtk.ResponseType.DELETE_EVENT:
            pass
        if response_id == \
            self.dialog.get_response_for_widget(self.revert_button):

            # revert changes and keep the dialog open
            self.theme = copy.deepcopy(self.original_theme)

            self.update_ui()
            self.theme.apply()
            return

        Gtk.main_quit()

    def update_ui(self):
        self.in_update = True

        self.update_key_styleList()
        self.update_color_schemeList()
        self.update_fontList()
        self.update_font_attributesList()
        self.background_gradient_scale.set_value(self.theme.background_gradient)
        self.key_roundness_scale.set_value(self.theme.roundrect_radius)
        self.key_size_scale.set_value(self.theme.key_size)
        self.key_fill_gradient_scale.set_value(self.theme.key_fill_gradient)
        self.key_stroke_gradient_scale. \
                set_value(self.theme.key_stroke_gradient)
        self.key_gradient_direction_scale. \
                set_value(self.theme.key_gradient_direction)
        self.key_shadow_strength_scale. \
                set_value(self.theme.key_shadow_strength)
        self.key_shadow_size_scale. \
                set_value(self.theme.key_shadow_size)
        self.update_superkey_labelList()
        self.superkey_label_size_checkbutton. \
                set_active(bool(self.theme.get_superkey_size_group()))

        self.update_sensivity()

        self.in_update = False

    def update_sensivity(self):
        self.revert_button.set_sensitive(not self.theme == self.original_theme)

        has_gradient = self.theme.key_style != "flat"
        self.gradients_box.set_sensitive(has_gradient)
        self.superkey_label_size_checkbutton.\
                      set_sensitive(bool(self.theme.get_superkey_label()))

    def update_key_styleList(self):
        self.key_style_list = Gtk.ListStore(str,str)
        self.key_style_combobox.set_model(self.key_style_list)
        cell = Gtk.CellRendererText()
        self.key_style_combobox.clear()
        self.key_style_combobox.pack_start(cell, True)
        self.key_style_combobox.add_attribute(cell, 'markup', 0)

        self.key_styles = [
                           # Key style with flat fill- and border colors
                           [_("Flat"), "flat"],
                           # Key style with simple gradients
                           [_("Gradient"), "gradient"],
                           # Key style for dish-like key caps
                           [_("Dish"), "dish"]
                           ]
        for name, id in self.key_styles:
            it = self.key_style_list.append((name, id))
            if id == self.theme.key_style:
                self.key_style_combobox.set_active_iter(it)

    def update_color_schemeList(self):
        self.color_scheme_list = Gtk.ListStore(str,str)
        self.color_scheme_combobox.set_model(self.color_scheme_list)
        cell = Gtk.CellRendererText()
        self.color_scheme_combobox.clear()
        self.color_scheme_combobox.pack_start(cell, True)
        self.color_scheme_combobox.add_attribute(cell, 'markup', 0)

        self.color_schemes = ColorScheme.get_merged_color_schemes()
        color_scheme_filename = self.theme.get_color_scheme_filename()
        for color_scheme in sorted(list(self.color_schemes.values()),
                                   key=lambda x: x.name):
            it = self.color_scheme_list.append((
                      format_list_item(color_scheme.name, color_scheme.is_system),
                      color_scheme.filename))
            if color_scheme.filename == color_scheme_filename:
                self.color_scheme_combobox.set_active_iter(it)

    def update_fontList(self):
        self.font_list = Gtk.ListStore(str,str)
        self.font_combobox.set_model(self.font_list)
        cell = Gtk.CellRendererText()
        self.font_combobox.clear()
        self.font_combobox.pack_start(cell, True)
        self.font_combobox.add_attribute(cell, 'markup', 0)
        self.font_combobox.set_row_separator_func(
                                    self.font_combobox_row_separator_func,
                                    None)

        # work around https://bugzilla.gnome.org/show_bug.cgi?id=654957
        # "SIGSEGV when trying to call Pango.Context.list_families twice"
        global font_families
        if not "font_families" in globals():
            widget = Gtk.DrawingArea()
            context = widget.create_pango_context()
            font_families = context.list_families()
            widget.destroy()

        families = [(font.get_name(), font.get_name()) \
                    for font in font_families]

        families.sort(key=lambda x: x[0])
        families = [(_("Default"), "Normal"),
                    ("-", "-")] + families
        fd = Pango.FontDescription(self.theme.key_label_font)
        family = fd.get_family()
        for f in families:
            it = self.font_list.append(f)
            if  f[1] == family or \
               (f[1] == "Normal" and not family):
                self.font_combobox.set_active_iter(it)

    def font_combobox_row_separator_func(self, model, iter, data):
        return unicode_str(model.get_value(iter, 0)) == "-"

    def update_font_attributesList(self):
        treeview = self.font_attributes_view

        if not treeview.get_columns():
            liststore = Gtk.ListStore(bool, str, str)
            self.font_attributes_list = liststore
            treeview.set_model(liststore)

            column_toggle = Gtk.TreeViewColumn("Toggle")
            column_text = Gtk.TreeViewColumn("Text")
            treeview.append_column(column_toggle)
            treeview.append_column(column_text)

            cellrenderer_toggle = Gtk.CellRendererToggle()
            column_toggle.pack_start(cellrenderer_toggle, False)
            column_toggle.add_attribute(cellrenderer_toggle, "active", 0)

            cellrenderer_text = Gtk.CellRendererText()
            column_text.pack_start(cellrenderer_text, True)
            column_text.add_attribute(cellrenderer_text, "text", 1)
            cellrenderer_toggle.connect("toggled",
                             self.on_font_attributesList_toggle, liststore)

        liststore = treeview.get_model()
        liststore.clear()

        fd = Pango.FontDescription(self.theme.key_label_font)
        items = [[fd.get_weight() == Pango.Weight.BOLD,
                  _("Bold"), "bold"],
                 [fd.get_style() == Pango.Style.ITALIC,
                  _("Italic"), "italic"],
                 [fd.get_stretch() == Pango.Stretch.CONDENSED,
                  _("Condensed"), "condensed"],
                ]
        for checked, name, id in items:
            it = liststore.append((checked, name, id))
            if id == "":
                treeview.set_active_iter(it)

    def update_superkey_labelList(self):
        # block premature signals when calling model.clear()
        self.superkey_label_combobox.set_model(None)

        self.superkey_label_model.clear()
        self.superkey_labels = [["",      _("Default")],
                                [_(""), _("Ubuntu Logo")]
                               ]

        for label, descr in self.superkey_labels:
            self.superkey_label_model.append((label, descr))

        label = self.theme.get_superkey_label()
        self.superkey_label_combobox.get_child().set_text(label \
                                                          if label else "")

        self.superkey_label_combobox.set_model(self.superkey_label_model)

    def on_background_gradient_value_changed(self, widget):
        value = float(widget.get_value())
        config.theme_settings.background_gradient = value
        self.theme.background_gradient = value
        self.update_sensivity()

    def on_key_style_combobox_changed(self, widget):
        value = self.key_style_list.get_value( \
                            self.key_style_combobox.get_active_iter(),1)
        self.theme.key_style = value
        config.theme_settings.key_style = value
        self.update_sensivity()

    def on_key_roundness_value_changed(self, widget):
        radius = int(widget.get_value())
        config.theme_settings.roundrect_radius = radius
        self.theme.roundrect_radius = radius
        self.update_sensivity()

    def on_key_size_value_changed(self, widget):
        value = int(widget.get_value())
        config.theme_settings.key_size = value
        self.theme.key_size = value
        self.update_sensivity()

    def on_color_scheme_combobox_changed(self, widget):
        filename = self.color_scheme_list.get_value( \
                               self.color_scheme_combobox.get_active_iter(),1)
        self.theme.set_color_scheme_filename(filename)
        config.theme_settings.color_scheme_filename = filename
        self.update_sensivity()

    def on_key_fill_gradient_value_changed(self, widget):
        value = int(widget.get_value())
        config.theme_settings.key_fill_gradient = value
        self.theme.key_fill_gradient = value
        self.update_sensivity()

    def on_key_stroke_gradient_value_changed(self, widget):
        value = int(widget.get_value())
        config.theme_settings.key_stroke_gradient = value
        self.theme.key_stroke_gradient = value
        self.update_sensivity()

    def on_key_gradient_direction_value_changed(self, widget):
        value = int(widget.get_value())
        config.theme_settings.key_gradient_direction = value
        self.theme.key_gradient_direction = value
        self.update_sensivity()

    def on_key_shadow_strength_value_changed(self, widget):
        value = float(widget.get_value())
        config.theme_settings.key_shadow_strength = value
        self.theme.key_shadow_strength = value
        self.update_sensivity()

    def on_key_shadow_size_value_changed(self, widget):
        value = float(widget.get_value())
        config.theme_settings.key_shadow_size = value
        self.theme.key_shadow_size = value
        self.update_sensivity()

    def on_font_combobox_changed(self, widget):
        if not self.in_update:
            self.store_key_label_font()
            self.update_sensivity()

    def on_font_attributesList_toggle(self, widget, path, model):
        model[path][0] = not model[path][0]
        self.store_key_label_font()
        self.update_sensivity()

    def store_key_label_font(self):
        font = self.font_list.get_value(self.font_combobox.get_active_iter(),1)
        for row in self.font_attributes_list:
            if row[0]:
                font += " " + row[2]

        self.theme.key_label_font = font
        config.theme_settings.key_label_font = font

    def on_superkey_label_combobox_changed(self, widget):
        self.store_superkey_label_override()
        self.update_sensivity()

    def on_superkey_label_size_checkbutton_toggled(self, widget):
        self.store_superkey_label_override()
        self.update_sensivity()

    def store_superkey_label_override(self):
        label = self.superkey_label_combobox.get_child().get_text()
        if sys.version_info.major == 2:
            label = label.decode("utf8")
        if not label:
            label = None   # removes the override
        checked = self.superkey_label_size_checkbutton.get_active()
        size_group = config.SUPERKEY_SIZE_GROUP if checked else ""
        self.theme.set_superkey_label(label, size_group)
        config.theme_settings.key_label_overrides = \
                                          dict(self.theme.key_label_overrides)

    def on_theme_notebook_switch_page(self, widget, gpage, page_num):
        ThemeDialog.current_page = page_num


class ScannerDialog(DialogBuilder):
    """ Scanner settings dialog """

    """ Input device columns """
    COL_ICON_NAME   = 0
    COL_DEVICE_NAME = 1
    COL_DEVICE      = 2

    """ Device mapping columns """
    COL_NAME    = 0
    COL_ACTION  = 1
    COL_BUTTON  = 2
    COL_KEY     = 3
    COL_VISIBLE = 4
    COL_WEIGHT  = 5

    """ UI strings for scan actions """
    action_names = { ScanMode.ACTION_STEP     : _("Step"),
                     ScanMode.ACTION_LEFT     : _("Left"),
                     ScanMode.ACTION_RIGHT    : _("Right"),
                     ScanMode.ACTION_UP       : _("Up"),
                     ScanMode.ACTION_DOWN     : _("Down"),
                     ScanMode.ACTION_ACTIVATE : _("Activate") }

    """ List of actions a profile supports """
    supported_actions = [ [ScanMode.ACTION_STEP],
                          [ScanMode.ACTION_STEP],
                          [ScanMode.ACTION_STEP,
                           ScanMode.ACTION_ACTIVATE],
                          [ScanMode.ACTION_LEFT,
                           ScanMode.ACTION_RIGHT,
                           ScanMode.ACTION_UP,
                           ScanMode.ACTION_DOWN,
                           ScanMode.ACTION_ACTIVATE] ]

    """ """
    keyvalues_LHalf = [Gdk.KEY_F1, Gdk.KEY_F2, Gdk.KEY_F3, Gdk.KEY_F4, Gdk.KEY_F5, Gdk.KEY_F6, Gdk.KEY_F7, Gdk.KEY_F8, Gdk.KEY_grave, Gdk.KEY_1, Gdk.KEY_2, Gdk.KEY_3, Gdk.KEY_4, Gdk.KEY_5, Gdk.KEY_6, Gdk.KEY_7, Gdk.KEY_8,  Gdk.KEY_9, Gdk.KEY_Tab, Gdk.KEY_q, Gdk.KEY_w, Gdk.KEY_e, Gdk.KEY_r, Gdk.KEY_t, Gdk.KEY_y, Gdk.KEY_u, Gdk.KEY_i, Gdk.KEY_o, Gdk.KEY_Caps_Lock, Gdk.KEY_a, Gdk.KEY_s, Gdk.KEY_d, Gdk.KEY_f, Gdk.KEY_g, Gdk.KEY_h, Gdk.KEY_j, Gdk.KEY_k, Gdk.KEY_l, Gdk.KEY_Shift_L, Gdk.KEY_z, Gdk.KEY_x, Gdk.KEY_c, Gdk.KEY_v, Gdk.KEY_b, Gdk.KEY_n, Gdk.KEY_m, Gdk.KEY_comma, Gdk.KEY_Control_L, Gdk.KEY_Super_L, Gdk.KEY_Alt_L, Gdk.KEY_space]
    
    keyvalues_RHalf = [Gdk.KEY_F9, Gdk.KEY_F10, Gdk.KEY_F11, Gdk.KEY_F12, Gdk.KEY_minus, Gdk.KEY_equal, Gdk.KEY_BackSpace, Gdk.KEY_Print, Gdk.KEY_Scroll_Lock, Gdk.KEY_Pause, Gdk.KEY_Num_Lock, Gdk.KEY_KP_Divide, Gdk.KEY_KP_Multiply, Gdk.KEY_KP_Subtract, Gdk.KEY_bracketleft, Gdk.KEY_bracketright, Gdk.KEY_backslash, Gdk.KEY_Insert, Gdk.KEY_Home, Gdk.KEY_Page_Up, Gdk.KEY_KP_7, Gdk.KEY_KP_8, Gdk.KEY_KP_9, Gdk.KEY_apostrophe, Gdk.KEY_Return, Gdk.KEY_Delete, Gdk.KEY_End, Gdk.KEY_Page_Down, Gdk.KEY_KP_4, Gdk.KEY_KP_5, Gdk.KEY_KP_6, Gdk.KEY_KP_Add, Gdk.KEY_slash, Gdk.KEY_Shift_R, Gdk.KEY_Up, Gdk.KEY_KP_1, Gdk.KEY_KP_2, Gdk.KEY_KP_3, Gdk.KEY_Super_R, Gdk.KEY_Menu, Gdk.KEY_Control_R, Gdk.KEY_Left, Gdk.KEY_Down, Gdk.KEY_Right, Gdk.KEY_KP_0, Gdk.KEY_KP_Decimal, Gdk.KEY_KP_Enter]

    def __init__(self):

        builder = LoadUI("settings_scanner_dialog")
        DialogBuilder.__init__(self, builder)

        self.device_manager = XIDeviceManager()
        self.device_manager.connect("device-event", self._on_device_event)
        self.pointer_selected = None
        self.mapping_renderer = None

        # order of execution is important
        self.init_input_devices()
        self.init_scan_modes()
        self.init_device_mapping()

        scanner = config.scanner
        self.bind_spin("cycles", scanner, "cycles")
        self.bind_spin("cycles_overscan", scanner, "cycles")
        self.bind_spin("cycles_stepscan", scanner, "cycles")
        self.bind_spin("step_interval", scanner, "interval")
        self.bind_spin("backtrack_interval", scanner, "interval")
        self.bind_spin("forward_interval", scanner, "interval_fast")
        self.bind_spin("backtrack_steps", scanner, "backtrack")
        self.bind_check("user_scan", scanner, "user_scan")
        self.bind_check("alternate", scanner, "alternate")
        self.bind_check("device_detach", scanner, "device_detach")

        """	#In: radio button	"""
        def on_key_type_toggle(radio, data, config_object, key):
            if radio.get_active():
                self._update_2_scan_ui()
                
                self.on_mapping_cleared(None, "clear", self.pointer_selected)
                
                if data == "multiple_key":
                    for action in self.supported_actions[config.scanner.mode]:
                        if action == ScanMode.ACTION_STEP:#In
                            for keyval in self.keyvalues_LHalf:#In
                                config.scanner.device_key_map[keyval] = action#In
                        else:
                            for keyval in self.keyvalues_RHalf:#In
                                config.scanner.device_key_map[keyval] = action#In
                
                    self.on_mapping_edited(None, "multiple", Gdk.KEY_Escape, self.pointer_selected)
        	        	
        self.bind_radio("single_key", config.scanner, "key_type", widget_callback = on_key_type_toggle)
        self.bind_radio("multiple_key", config.scanner, "key_type", widget_callback = on_key_type_toggle)
        
        self._update_2_scan_ui()
        """	#In: radio button	"""
        
    def __del__(self):
        _logger.debug("ScannerDialog.__del__()")

    def run(self, parent):
        dialog = self.wid("dialog")
        dialog.set_transient_for(parent)
        dialog.run()
        dialog.destroy()

        config.scanner.disconnect_notifications()
        self.device_manager = None

    def init_scan_modes(self):
        combo = self.wid("scan_mode_combo")
        combo.set_active(config.scanner.mode)
        combo.connect("changed", self.on_scan_mode_changed)
        config.scanner.mode_notify_add(self._scan_mode_notify)
        self.wid("scan_mode_notebook").set_current_page(config.scanner.mode)

    def on_scan_mode_changed(self, widget):
        config.scanner.mode = widget.get_active()

    def _scan_mode_notify(self, mode):
        self.wid("scan_mode_combo").set_active(mode)
        self.wid("scan_mode_notebook").set_current_page(mode)
        
        """ In: radio button """
        if mode == 2 and self.wid("device_detach").get_sensitive() == True:#stepscan
            config.scanner.key_type = "single_key"
            self.wid(config.scanner.key_type).set_active(True)
            self.wid("key_type_grid").show()
            """The below line exists here to resest when mode is changed; and not when @ regular start-up once you had set i.e. in case of same if-else in above function"""
        else:
            self.wid("key_type_grid").hide()
        
        self.update_device_mapping()

    def init_input_devices(self):
        combo = self.wid("input_device_combo")
        combo.set_model(Gtk.ListStore(str, str, GObject.TYPE_PYOBJECT))
        combo.add_attribute(self.wid("input_device_icon_renderer"),
                            "icon-name", self.COL_ICON_NAME)
        combo.add_attribute(self.wid("input_device_text_renderer"),
                            "text", self.COL_DEVICE_NAME)

        self.update_input_devices()

        combo.connect("changed", self.on_input_device_changed)
        config.scanner.device_name_notify_add(self._device_name_notify)

    def update_input_devices(self):
        devices = self.list_devices()
        model = self.wid("input_device_combo").get_model()
        model.clear()

        model.append(["input-mouse", ScanDevice.DEFAULT_NAME, None])

        for dev in devices:
            if dev.is_pointer():
                model.append(["input-mouse", dev.name, dev])

        for dev in devices:
            if not dev.is_pointer():
                model.append(["input-keyboard", dev.name, dev])

        self.select_current_device(config.scanner.device_name)

    def select_current_device(self, name):
        combo = self.wid("input_device_combo")
        model = combo.get_model()
        it = model.get_iter_first()
        if it is None:
            return

        self.wid("key_type_grid").hide()
        
        if name == ScanDevice.DEFAULT_NAME:
            self.pointer_selected = True
            self.wid("device_detach").set_sensitive(False)
            combo.set_active_iter(it)
        else:
            while it:
                device = model.get_value(it, self.COL_DEVICE)
                if device and name == device.get_config_string():
                    self.pointer_selected = device.is_pointer()
                    self.wid("device_detach").set_sensitive(True)
                    if config.scanner.mode == 2:#stepscan
                        self.wid("key_type_grid").show()
                    combo.set_active_iter(it)
                    break
                it = model.iter_next(it)

        if self.mapping_renderer:
            self.mapping_renderer.set_property("pointer-mode",
                                               self.pointer_selected)

    def on_input_device_changed(self, combo):
        model = combo.get_model()
        it = combo.get_active_iter()
        if it is None:
            return

        config.scanner.device_detach = False
        device = model.get_value(it, self.COL_DEVICE)

        if device:
            config.scanner.device_name = device.get_config_string()
            self.wid("device_detach").set_sensitive(True)
            if config.scanner.mode == 2:#stepscan
                config.scanner.key_type = "single_key"
                self.wid(config.scanner.key_type).set_active(True)
                self.wid("key_type_grid").show()
                """The below line exists here to resest when mode is changed; and not when @ regular start-up once you had set i.e. in case of same if-else in above function"""
            self.pointer_selected = device.is_pointer()
        else:
            config.scanner.device_name = ScanDevice.DEFAULT_NAME
            self.wid("device_detach").set_sensitive(False)
            self.wid("key_type_grid").hide()
            self.pointer_selected = True

        if self.mapping_renderer:
            self.mapping_renderer.set_property("pointer-mode",
                                               self.pointer_selected)

    def _device_name_notify(self, name):
        self.select_current_device(name)
        self.update_device_mapping()

    def init_device_mapping(self):
        self.update_device_mapping()

        self.mapping_renderer = CellRendererMapping()
        self.mapping_renderer.set_property("pointer-mode", self.pointer_selected)
        self.mapping_renderer.connect("mapping-edited", self.on_mapping_edited)
        self.mapping_renderer.connect("mapping-cleared", self.on_mapping_cleared)

        column = self.wid("column_mapping")
        column.pack_start(self.mapping_renderer, False)
        column.add_attribute(self.mapping_renderer, "button", self.COL_BUTTON)
        column.add_attribute(self.mapping_renderer, "key", self.COL_KEY)
        column.add_attribute(self.mapping_renderer, "visible", self.COL_VISIBLE)

    def update_device_mapping(self):
        view = self.wid("device_mapping")
        model = view.get_model()
        model.clear()

        parent_iter = model.append(None)
        model.set(parent_iter,
                  self.COL_NAME, _("Action:"),
                  self.COL_WEIGHT, Pango.Weight.BOLD)

        for action in self.supported_actions[config.scanner.mode]:
            child_iter = model.append(parent_iter)
            model.set(child_iter,
                      self.COL_NAME, self.action_names[action],
                      self.COL_ACTION, action,
                      self.COL_VISIBLE, True,
                      self.COL_WEIGHT, Pango.Weight.NORMAL)

            if self.pointer_selected:
                button = self.get_value_for_action \
                    (action, config.scanner.device_button_map)
                if button:
                    model.set(child_iter, self.COL_BUTTON, button)
            else:
                key = self.get_value_for_action \
                    (action, config.scanner.device_key_map)
                if key:
                    model.set(child_iter, self.COL_KEY, key)

        view.expand_all()

    def on_mapping_edited(self, cell, path, value, pointer_mode):
        if path == "multiple":#for radio button mapping to work
        	path = "0:0"
        	pass
        
        model = self.wid("device_mapping_model")
        it = model.get_iter_from_string(path)
        if it is None:
            return

        if pointer_mode:
            col = self.COL_BUTTON
            dev_map = config.scanner.device_button_map.copy()
        else:
            col = self.COL_KEY
            dev_map = config.scanner.device_key_map.copy()

        dup_it = model.get_iter_from_string("0:0")
        dup_val = None
        while dup_it:
            if value == model.get_value(dup_it, col):
                dup_val = model.get_value(dup_it, col)
                model.set(dup_it, col, 0)
                break
            dup_it = model.iter_next(dup_it)

        model.set(it, col, value)

        if config.scanner.mode == 2 and config.scanner.key_type == "multiple_key":
            model.set(it, col, 0)

        if dup_val in dev_map:
            del dev_map[dup_val]

        action = model.get_value(it, self.COL_ACTION)
        dev_map[value] = action

        for k, v in dev_map.items():
            if k != value and v == action:
                del dev_map[k]
                break

        if pointer_mode:
            config.scanner.device_button_map = dev_map
        else:
            config.scanner.device_key_map = dev_map

    def on_mapping_cleared(self, cell, path, pointer_mode):
        """ In: radio button """
        if path == "clear":
            if len(config.scanner.device_key_map) > 0:
                config.scanner.device_key_map = {}
                model = self.wid("device_mapping_model")
                model.set(model.get_iter_from_string("0:0"), self.COL_KEY, 0)
                model.set(model.get_iter_from_string("0:1"), self.COL_KEY, 0)
            return
        """ In: radio button """
        
        model = self.wid("device_mapping_model")
        it = model.get_iter_from_string(path)
        if it is None:
            return

        if pointer_mode:
            old_value = model.get_value(it, self.COL_BUTTON)
            model.set(it, self.COL_BUTTON, 0)
            if old_value in config.scanner.device_button_map:
                copy = config.scanner.device_button_map.copy()
                del copy[old_value]
                config.scanner.device_button_map = copy
        else:
            old_value = model.get_value(it, self.COL_KEY)
            model.set(it, self.COL_KEY, 0)
            if old_value in config.scanner.device_key_map:
                copy = config.scanner.device_key_map.copy()
                del copy[old_value]
                config.scanner.device_key_map = copy

    def list_devices(self):
        return [d for d in self.device_manager.get_devices() \
                if ScanDevice.is_useable(d) ]

    def _on_device_event(self, event):
        if event.xi_type in [XIEventType.DeviceAdded,
                             XIEventType.DeviceRemoved]:
            self.update_input_devices()

    def get_value_for_action(self, action, dev_map):
        for k, v in dev_map.items():
            if v == action:
                return k

    """ In: radio button """
    def _update_2_scan_ui(self):
        if config.scanner.key_type == "multiple_key" and config.scanner.mode == 2:#only for stepscan
            self.wid("scrolledwindow1").set_sensitive(False)
        else: #config.scanner.key_type == "single_key"
            self.wid("scrolledwindow1").set_sensitive(True)


MAX_GINT32 = (1 << 31) - 1

class CellRendererMapping(Gtk.CellRendererText):
    """
    Custom cell renderer that displays device buttons as labels.
    """

    __gproperties__ = { str('button')      : (GObject.TYPE_INT,
                                             '', '', 0, MAX_GINT32, 0,
                                             GObject.PARAM_READWRITE),
                        str('key')         : (GObject.TYPE_INT,
                                             '', '', 0, MAX_GINT32, 0,
                                             GObject.PARAM_READWRITE),
                        str('pointer-mode') : (bool, '', '', True,
                                             GObject.PARAM_READWRITE) }

    __gsignals__ = { str('mapping-edited') : (GObject.SIGNAL_RUN_LAST,
                                             None, (str, int, bool)),
                     str('mapping-cleared'): (GObject.SIGNAL_RUN_LAST,
                                             None, (str, bool)) }

    def __init__(self):
        super(CellRendererMapping, self).__init__(editable=True)

        self.key = 0
        self.button = 0
        self.pointer_mode = True

        self._edit_widget = None
        self._grab_widget = None
        self._grab_pointer = None
        self._grab_keyboard = None
        self._path = None
        self._bp_id = 0
        self._kp_id = 0
        self._se_id = 0
        self._sizing_label = None

        self._update_text_props()

    def do_get_property(self, prop):
        if prop.name == 'button':
            return self.button
        elif prop.name == 'key':
            return self.key
        elif prop.name == 'pointer-mode':
            return self.pointer_mode

    def do_set_property(self, prop, value):
        if prop.name == 'button':
            self.button = value
        elif prop.name == 'key':
            self.key = value
        elif prop.name == 'pointer-mode':
            self.pointer_mode = value

        self._update_text_props()

    def _update_text_props(self):

        if (self.pointer_mode and self.button == 0) or \
           (not self.pointer_mode and self.key == 0):
            self.set_property("style", Pango.Style.ITALIC)
            self.set_property("foreground-rgba", Gdk.RGBA(0.6, 0.6, 0.6, 1.0))
            text = _("Disabled")
        else:
            self.set_property("style", Pango.Style.NORMAL)
            self.set_property("foreground-set", False)

            if self.pointer_mode:
                text = "{} {!s}".format(_("Button"), self.button)
            else:
                text = Gdk.keyval_name(self.key)

        if config.scanner.mode == 2 and config.scanner.key_type == "multiple_key":
            text = _("Disabled")

        self.set_property("text", text)

    def _on_edit_widget_unrealize(self, widget):
        Gtk.device_grab_remove(self._grab_widget, self._grab_pointer)

        time = Gtk.get_current_event_time()
        self._grab_pointer.ungrab(time)
        self._grab_keyboard.ungrab(time)

    def _editing_done(self):
        self._grab_widget.handler_disconnect(self._bp_id)
        self._grab_widget.handler_disconnect(self._kp_id)
        self._grab_widget.handler_disconnect(self._se_id)
        self._edit_widget.editing_done()
        self._edit_widget.remove_widget()

    def _on_button_press(self, widget, event):
        self._editing_done()

        if self.pointer_mode:
            self.emit("mapping-edited",
                      self._path, event.button, self.pointer_mode)
        return True

    def _on_key_press(self, widget, event):
        self._editing_done()

        value = Gdk.keyval_to_lower(event.keyval)

        if value == Gdk.KEY_BackSpace:
            self.emit("mapping-cleared", self._path, self.pointer_mode)
        elif value == Gdk.KEY_Escape:
            pass
        else:
            if not self.pointer_mode:
                self.emit("mapping-edited",
                          self._path, value, self.pointer_mode)
        return True

    def _on_scroll_event(self, widget, event):
        self._editing_done()

        if self.pointer_mode:
            # mouse buttons 4 - 7 are delivered as scroll-events
            button = 4 + event.direction
            self.emit("mapping-edited",
                      self._path, button, self.pointer_mode)
        return True

    def do_get_preferred_width(self, widget):
        if self._sizing_label is None:
            self._sizing_label = Gtk.Label(label=_("Press a button..."))

        return self._sizing_label.get_preferred_width()

    def do_start_editing(self, event, widget, path, bg_area, cell_area, state):
        if not event: # else SEGFAULT when pressing a keyboard key twice
            return

        time = event.get_time()
        device = event.get_device()
        if device.get_source() == Gdk.InputSource.KEYBOARD:
            keyboard = device
            pointer = device.get_associated_device()
        else:
            pointer = device
            keyboard = device.get_associated_device()

        if keyboard.grab(widget.get_window(),
                         Gdk.GrabOwnership.WINDOW, False,
                         Gdk.EventMask.KEY_PRESS_MASK,
                         None, time) != Gdk.GrabStatus.SUCCESS:
            return

        if pointer.grab(widget.get_window(),
                        Gdk.GrabOwnership.WINDOW, False,
                        Gdk.EventMask.BUTTON_PRESS_MASK,
                        None, time) != Gdk.GrabStatus.SUCCESS:
            keyboard.ungrab(time)
            return

        Gtk.device_grab_add(widget, pointer, True)

        self._path = path
        self._grab_pointer = pointer
        self._grab_keyboard = keyboard
        self._grab_widget = widget
        self._bp_id = widget.connect("button-press-event", self._on_button_press)
        self._kp_id = widget.connect("key-press-event", self._on_key_press)
        self._se_id = widget.connect("scroll-event", self._on_scroll_event)

        style = widget.get_style_context()
        bg = style.get_background_color(Gtk.StateFlags.SELECTED)
        fg = style.get_color(Gtk.StateFlags.SELECTED)

        if self.pointer_mode:
            text = _("Press a button...")
        else:
            text = _("Press a key...")

        label = Gtk.Label(label=text,
                          halign=Gtk.Align.START,
                          valign=Gtk.Align.CENTER)
        label.override_color(Gtk.StateFlags.NORMAL, fg)

        self._edit_widget = EditableBox(label)
        self._edit_widget.override_background_color(Gtk.StateFlags.NORMAL, bg)
        self._edit_widget.connect("unrealize", self._on_edit_widget_unrealize)
        self._edit_widget.show_all()

        return self._edit_widget


class EditableBox(Gtk.EventBox, Gtk.CellEditable):
    """
    Container that implements the Gtk.CellEditable interface.
    """

    __gproperties__ = { str('editing-canceled'): (bool, '', '', False,
                                                  GObject.PARAM_READWRITE) }

    def __init__(self, child=None):
        super(EditableBox, self).__init__()

        self.editing_canceled = False

        if child:
            self.add(child)

    def do_get_property(self, prop):
        if prop.name == 'editing-canceled':
            return self.editing_canceled

    def do_set_property(self, prop, value):
        if prop.name == 'editing-canceled':
            self.editing_canceled = value

    def do_start_editing(self, event):
        pass


if __name__ == '__main__':
    s = Settings(True)

