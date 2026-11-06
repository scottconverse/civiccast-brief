"""Glue between the stages: a meeting run into the producer screen, an approved item into the site.

    stage     meeting folder + checked script  ->  a draft item in the review database
    assemble  exported (approved) item + meeting folder + audio folder  ->  one site episode JSON

``assemble`` refuses an item whose exported recap or script differs from the files it would
publish, so the producer's approval always covers exactly what reaches the site.
"""
