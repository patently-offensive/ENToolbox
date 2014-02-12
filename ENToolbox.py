from bs4 import BeautifulSoup

import datetime
import time
import hashlib
import re
import string
from types import *

import os
import sys

from evernote.api.client import EvernoteClient
import evernote.edam.type.ttypes as Types
from evernote.edam.notestore import NoteStore
import evernote.edam.error.ttypes as ENErrors

def _get_user_store(dev_token):	
	client = EvernoteClient(token=dev_token, sandbox=False)
	userStore = client.get_user_store()
	
	return userStore


def _get_note_store(dev_token):
	client = EvernoteClient(token=dev_token, sandbox=False)
	noteStore = client.get_note_store()
	
	return noteStore


def _get_stores(dev_token):
	"""
	Returns (userStore, noteStore) tuple.
	"""
	# FIXME - fix hardcoded dev token; implement OAuth

	return _get_user_store(dev_token), _get_note_store(dev_token)


def _get_ocr(dev_token, noteStore, note):
	"""
	Returns a list of dictonaries populated 
	by OCR data from each note in the notebook tagged with the tag.
	
	Returned dictionaries hav keys:
		'matter_no' (as list of matter number components, e.g., 0001.01US = ['0001', '01US'])
		'type' (Final/Non-final)
		'date' (as datetime object),
		'examiner' (Examiner's name as string),
	"""
	import evernote.edam.error.ttypes as Errors

	r = note.resources[0]
	try:
		recog_data = noteStore.getResourceRecognition(dev_token, r.guid)
	except Errors.EDAMNotFoundException, ednfe:
		raise
		return

	soup = BeautifulSoup(recog_data, 'lxml')

	return parse_ocr_items(soup.findAll('item'))


def _parse_ocr_items(items, field_coords, fudge=50, threshold=50):
	"""
	Takes a list of OCR items and a dictionary of field coordinates
	and returns a dectionary. 
	"""
	fudge = fudge / 2
	fields = {}

	for field, coords in field_coords.items():

		field_x_range = range(coords[0] - fudge, coords[0] + fudge)
		field_y_range = range(coords[1] - fudge, coords[1] + fudge)

		for item in items:
			item_x = int(item['x'])
			item_y = int(item['y'])

			if (item_x in field_x_range) and (item_y in field_y_range):
				max_w = 0
				for t in item.findAll('t'):
					weight = int(t['w'])

					if weight > threshold and weight > max_w:
						t_val = str(t.string)
						fields[field] = t_val
						max_w = weight
					else:
						continue
		else:
			if field not in fields:
				fields[field] = None

	return fields


def _create_notebook(dev_token, noteStore, notebook_name):
	nb = Types.Notebook()
	nb.name = notebook_name

	return noteStore.createNotebook(dev_token, nb)


def _get_notebook_by_name(dev_token, noteStore, notebook_name, create=False):
	"""
	Returns notebook with provided name. If create=true, notebook will be
	created if it is not found.
	"""
	notebooks = noteStore.listNotebooks(dev_token)
	for notebook in notebooks:
		if notebook.name == notebook_name: return notebook
	else:
		if create:
			return _create_notebook(dev_token, noteStore, notebook_name)
		else:
			return None


def _create_tag(dev_token, noteStore, tag_name):
	t = Types.Tag()
	t.name = tag_name
	
	return noteStore.createTag(dev_token, t)


def _get_tag_by_name(dev_token, noteStore, tag_name, create=False):
	"""
	Returns tag with provided name. If create=true, tag will be
	created if it is not found.
	"""
	tags = noteStore.listTags(dev_token)
	for tag in tags:
		if tag.name == tag_name: return tag
	else:
		if create:
			return _create_tag(dev_token, noteStore, tag_name)
		else:
			return None


def _make_reminder_time(dt):
	allowed_types = (datetime.datetime, datetime.date, datetime.time)
	assert isinstance(dt, allowed_types), "reminder is not a datetime object: %r" % dt
	reminder_time = (dt - datetime.datetime(1970, 1, 1)).total_seconds() * 1000
	return reminder_time


def _make_note(authToken, noteStore, noteTitle, noteBody, 
				noteResource=None, parentNotebook=None, tags=None, reminder=None):
	import evernote.edam.error.ttypes as Errors

	nBody = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
	nBody += "<!DOCTYPE en-note SYSTEM \"http://xml.evernote.com/pub/enml2.dtd\">"
	nBody += '<en-note>' + noteBody

	# Create note object
	ourNote = Types.Note()
	ourNote.title = noteTitle

	if noteResource is not None:
		nBody += '<br/>' * 2
		resource_name = string.split(noteResource, '/')[-1]
		resource_ext = string.split(resource_name, '.')[-1]
		if resource_ext in ['png', 'gif', 'jpeg']:
			mime_prefix = 'image'
		elif resource_ext == 'pdf':
			mime_prefix = 'application'
		else:
			mime_prefix = 'unknown'

		# Calculate the md5 hash of the resource file
		md5 = hashlib.md5()
		with open(noteResource,'rb') as f:
			resource_bytes = f.read()
		md5.update(resource_bytes)
		md5hash = md5.hexdigest()

		# Create the Data type for evernote that goes into a resource
		resource_data = Types.Data()
		resource_data.bodyHash = md5hash
		resource_data.size = len(resource_bytes) 
		resource_data.body = resource_bytes

		# Create a resource for the note that contains the resource
		resource = Types.Resource()
		resource.data = resource_data
		resource.mime = "%s/%s" % (mime_prefix, resource_ext)

		# Create a resource list to hold the pdf resource
		resource_list = []
		resource_list.append(resource)

		# Set the note's resource list
		ourNote.resources = resource_list

		# Add a link in the evernote body for this content
		nBody += '<en-media type="%s" hash="%s"/>' % (resource.mime, resource_data.bodyHash)

	nBody += '</en-note>'
	ourNote.content = nBody
	
	# parentNotebook is optional; if omitted, default notebook is used
	if parentNotebook is not None:
		pn = _get_notebook_by_name(authToken, noteStore, parentNotebook, create=True)
		ourNote.notebookGuid = pn.guid

	if tags is not None:
		tag_guids = [_get_tag_by_name(authToken, noteStore, \
			tag, create=True).guid for tag in tags]
		ourNote.tagGuids = tag_guids

	if reminder is not None:
		reminder_time = _make_reminder_time(reminder)
		now = int(round(time.time() * 1000)) 
		ourNote.attributes = Types.NoteAttributes()
		ourNote.attributes.reminderTime = reminder_time
		ourNote.attributes.reminderOrder = now

	# Attempt to create note in Evernote account
	try:
		note = noteStore.createNote(authToken, ourNote)
	except Errors.EDAMUserException:
		# Something was wrong with the note data
		# See EDAMErrorCode enumeration for error code explanation
		# http://dev.evernote.com/documentation/reference/Errors.html#Enum_EDAMErrorCode
		return None
	except Errors.EDAMNotFoundException:
		# Parent Notebook GUID doesn't correspond to an actual notebook
		return None

	return note


class Toolbox(object):

	def __init__(self, dev_token):
		self.uStore, self.nStore = _get_stores(dev_token)
		self.token = dev_token
		self.updateCount = self.nStore.getSyncState(self.token).updateCount
		self.errors = ENErrors

	def new_data(self, last_update_count):
		# Replace None w/0
		last_update_count = int(last_update_count) or 0

		return self.updateCount > last_update_count

	def note_app_data(self, note, app, key, value=None):
		"""
		Retrieves or sets a note's application data 
		depending on whether value is provided.
		"""
		if value is not None:
			# Fetch the application data as a dictionary
			try:
				app_data_dict = eval(self.nStore.getNoteApplicationDataEntry(self.token, \
					note.guid, app))
			except ENErrors.EDAMNotFoundException:
				app_data_dict = {}

			# Update application data
			app_data_dict[key] = value

			self.nStore.setNoteApplicationDataEntry(self.token, note.guid, app, \
				repr(app_data_dict))

			return note
		else:	
			# Retrieve the application data as a dictionary
			try:
				app_data_dict = eval(self.nStore.getNoteApplicationDataEntry(self.token, note.guid, app))
			except ENErrors.EDAMNotFoundException:
				return None

			# Retrieve the value at the specified key within the dictionary
			try:
				value = app_data_dict[key]
			except KeyError:
				return None
			else:
				return value


	def note_prepend(self, note, added_content):
		"""
		Adds content to the top of a note.
		"""
		# Get note content; get note content body
		existing_content = self.nStore.getNoteContent(self.token, note.guid)
		soup = BeautifulSoup(existing_content, 'lxml')
		body = soup.find('en-note')

		# Insert content
		body.insert(0, soup.new_tag('br'))
		body.insert(0, added_content)

		# Format new note content
		new_content = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
		new_content += "<!DOCTYPE en-note SYSTEM \"http://xml.evernote.com/pub/enml2.dtd\">"
		new_content += str(body)

		note.content = new_content

		return self.nStore.updateNote(self.token, note)


	def note_append(self, note, added_content):
		"""
		Adds content to the bottom of a note.
		"""
		# Get note content; get note content body
		existing_content = self.nStore.getNoteContent(self.token, note.guid)
		soup = BeautifulSoup(existing_content, 'lxml')
		body = soup.find('en-note')

		# Insert content
		body.append(soup.new_tag('br'))
		body.append(added_content)

		# Format new note content
		new_content = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
		new_content += "<!DOCTYPE en-note SYSTEM \"http://xml.evernote.com/pub/enml2.dtd\">"
		new_content += str(body)

		note.content = new_content

		return self.nStore.updateNote(self.token, note)


	def note_parse_ocr(self, note, field_coords, r_index=0, fudge=50, threshold=50):
		try:
			r = note.resources[r_index]
		except ENErrors.EDAMNotFoundException:
			raise
			return None

		try:
			recog_data = self.nStore.getResourceRecognition(self.token, r.guid)
		except ENErrors.EDAMNotFoundException:
			raise
			return None

		soup = BeautifulSoup(recog_data, 'lxml')
		items = soup.findAll('item')
		
		return _parse_ocr_items(items, field_coords, fudge=50, threshold=50)


	def make_note(self, noteTitle, noteBody, 
		resource=None, notebook=None, tags=None, reminder=None):
		
		note = _make_note(self.token, self.nStore, noteTitle, noteBody, \
			resource, notebook, tags, reminder)

		return note


	def find(self, notebook=None, tags=None, words=None):
		noteFilter = NoteStore.NoteFilter()

		if notebook is not None:
			notebook = _get_notebook_by_name(self.token, self.nStore, notebook)
			try:
				noteFilter.notebookGuid = notebook.guid
			except AttributeError: # Provided notebook doesn't exist, return empty results
				return []

		if tags is not None:
			try:
				noteFilter.tagGuids = [_get_tag_by_name(self.token, self.nStore, \
					t).guid for t in tags]
			except AttributeError: # One of the provided tags doesn't exist, return empty results
				return []

		if words is not None:
			noteFilter.words = words

		offset = 0
		notes = []
		while True:
			results = self.nStore.findNotes(self.token, noteFilter, offset, 50)
			notes += results.notes

			if len(notes) < results.totalNotes:
				offset += 50
				continue
			else:
				return notes


	def dir_links(self, path):
		soup = BeautifulSoup('<ul></ul>', 'lxml')
		sublist = soup.ul
		for root, subFolders, files in os.walk(path):
			link = 'file://' + root
			name = root.split('/')[-1]

			item_tag = soup.new_tag('li')
			a_tag = soup.new_tag('a', href=link)
			a_tag.string = name.split('.')[0]
			item_tag.append(a_tag)
			sublist.append(item_tag)

			sublist_tag = soup.new_tag('ul')
			item_tag.append(sublist_tag)


			for f in files:
				link = 'file://' + root + f

				item_tag = soup.new_tag('li')
				a_tag = soup.new_tag('a', href=link)
				a_tag.string = f
				item_tag.append(a_tag)
				sublist_tag.append(item_tag)

			# If next os.walk iteration is going down a level, go down a level in list
			if subFolders:
				sublist = sublist.ul

		return soup.ul


	def db_dir_links(self, db_metadata, local_prefix):
		"""
		Receives db metadata, returns html list w/links to all files for insertion into EN note.
		"""
		# FIXME - take in dbClient; during recursive step when folder is found, pass metadata of folder
		# if soup is None:
		soup = BeautifulSoup('<ul></ul>', 'lxml')

		path = db_metadata['path']
		link = 'file://' + local_prefix + path
		name = path.split('/')[-1]


		item_tag = soup.new_tag('li')
		a_tag = soup.new_tag('a', href=link)
		a_tag.string = name.split('.')[0]
		item_tag.append(a_tag)
		soup.ul.append(item_tag)

		contents = db_metadata['contents']

		sublist_tag = soup.new_tag('ul')
		item_tag.append(sublist_tag)

		for file_dict in contents:
			path = file_dict['path']
			name = path.split('/')[-1]

			if file_dict['is_dir'] == True:
				if name != 'References': 
					sublist_tag.append(self.db_links(path, local_prefix))
				continue

			link = 'file://' + local_prefix + path
			item_tag = soup.new_tag('li')
			a_tag = soup.new_tag('a', href=link)
			a_tag.string = name.split('.')[0]
			item_tag.append(a_tag)
			sublist_tag.append(item_tag)

		else:
			return soup.ul


	def note_pop(self, note, field_dict, in_place=False, \
		resource=None, notebook=None, tags=None, reminder=None):
		"""
		Populates a template note containint fields according to @field_dict. 
		@field_dict should be a dictionary consisting of {field : value} pairs, where
		occurrences of field are to be replaced with value.

		If @in_place is set to True, the template note will be populated in place.
		That is, the template note will be *replaced* with a populated version. 
		If it is set to False, a new populated note will be created according to the 
		@resource, @notebook, @tags, and @reminder parameters.
		"""
		content = self.nStore.getNoteContent(self.token, note.guid)
		title = str(note.title)
		
		soup = BeautifulSoup(content, 'lxml')
		body = soup.find('en-note')
		
		new_body = body
		new_title = title

		for k, v in field_dict.items():
			# # Values must be strings
			# assert type(v) is StringType

			# Make replacements in title
			try:
				new_title = new_title.replace(k, v)
			except TypeError:
				pass

			# Make replacements in body
			targets = new_body(text=re.compile(k))
			for target in targets:
				try:
					target.replace_with(target.replace(k, v))
				except TypeError: # Reached html data
					target.replace_with(v)


		if in_place:
			# Flatten new body
			new_body = ''.join([str(tag) for tag in new_body.contents])
		
			new_content = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
			new_content += "<!DOCTYPE en-note SYSTEM \"http://xml.evernote.com/pub/enml2.dtd\">"
			new_content += '<en-note>' + new_body + '</en-note>'

			# Update note content
			note.content = new_content
			return self.nStore.updateNote(self.token, note)
		else:
			# Strip existing resource links
			links = new_body('en-media')
			for link in links:
				link.decompose()

			# Flatten new body
			new_body = ''.join([str(tag) for tag in new_body.contents])
			# Make new note
			return _make_note(self.token, self.nStore, new_title, new_body, \
				resource, notebook, tags, reminder)

		
	def note_update(self, note):
		return self.nStore.updateNote(self.token, note)

	def note_reminder(self, note, reminder):
		reminder_time = _make_reminder_time(reminder)
		note.attributes.reminderTime = reminder_time

		return self.nStore.updateNote(self.token, note)


def main(dev_token):
	TB = Toolbox(dev_token)

	title = 'Test title'
	body = 'Test body'
	notebook = 'TEST NOTEBOOK'
	res = '/Users/skammlade/Dropbox/3pt/test_resource.png'
	tags = ['*test1', '*test2']
	field = '<<OCR Data>>'
	field_f = '{{FILES}}'
	f_path = '/Users/skammlade/Desktop/Workspace/0037/'
	reminder = datetime.datetime.now() + datetime.timedelta(7)

	field_coords = {
				'app_no' 	  :	(282, 588),		# Application no.
				'filing_date' : (658, 590),		# Application filing date
				'matter_no'   :	(1768, 588),	# Matter number
				'inventor_fn' :	(1195, 590),	# Inventor's first name
				'inventor_ln' :	(1260, 590),	# Inventor's last name
				'confirm_no'  :	(2190, 590),	# Confirmation number
				'examiner_ln' :	(1828, 754),	# Examiner's last name
				'examiner_fn' :	(2010, 754),	# Examiner's first name
				'art_unit'	  :	(1818, 898), 	# Art unit
				'date'	  	:	(1770, 1084)	# Office Action date
				}

	results = TB.find(notebook=notebook, tags=tags, words=field)

	if results != []: # Found the note
		for note in results:
			try:
				OCR = TB.note_parse_ocr(note, field_coords)
			except ENErrors.EDAMNotFoundException:
				continue
			else:
				TB.note_pop(note, {field : repr(OCR), field_f : TB.dir_links(f_path)}, in_place=False, \
					notebook=None, tags=['*test3', '*test4'], \
					reminder=datetime.datetime.now())
				note = TB.note_pop(note, {field : repr(OCR)}, in_place=True)
				TB.note_reminder(note, datetime.datetime.now())
	else:
		note = TB.make_note(title, body, resource=res, notebook=notebook, \
			tags=tags, reminder=reminder)

		app = 'Test_app'
		key = 'Test key'
		value = 'Test value'
		TB.note_app_data(note, app, key, value)

		app_data = TB.note_app_data(note, app, key)
		TB.note_prepend(note, 'Data for application %s, key %s : %s (should be %s)' % (app, key, app_data, value))

		target = 'Actions pending'
		count = len(TB.find(notebook=target))
		s = 'There are [%i] notes in the [%s] notebook.' % (count, target)
		TB.note_prepend(note, s)

		TB.note_append(note, 'OCR data: %s' % field)

		TB.note_prepend(note, 'Files: %s' % field_f)

	return

if __name__ == '__main__':
	from configobj import ConfigObj
	config = ConfigObj('config')
	dev_token = config['Evernote']['dev_token']
	main(dev_token)