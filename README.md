# Women's Volleyball Rosters

This repository contains roster information from more than 900 NCAA women's volleyball teams, including every active team from Division I, II and III. It was collected as part of a student project in JOUR479X, Sports Data Analysis and Visualization at the University of Maryland, with Alexa Henry, a graduate student in journalism.

This project was supervised and edited by Derek Willis, lecturer in data and computational journalism.

The original roster information is drawn from team websites, mostly obtained via [scrapers written in Python](https://github.com/Sports-Roster-Data/womens-volleyball/blob/main/rosters.py). That information has been augmented by individual research and editing. For example, a transfer player's high school may not be noted on her current team, but it could be found on her previous team's roster. Team information comes from [the NCAA](https://stats.ncaa.org/rankings?academic_year=2024&division=1.0&sport_code=WVB).

The roster data in this repository has been cleaned and standardized by contributors using R; a description of that process and the code is available in [this RMarkdown Notebook](cleaning.Rmd). 

The specific information cleaned and parsed includes the following:

* Position (also standardized)
* Height (standardized and converted to total inches to make comparisons possible)
* Year (also standardized; because this information could refer to either academic or athletic eligibility, it may not be reflective of one of those - and COVID eligibility added more complexity)
* Hometown (parsed using the [postmastr](https://slu-opengis.github.io/postmastr/) package, then separated into hometown, state and country-specific fields. For foreign countries, we started with the list of FIBA nations and added others, then standardized the results)
* Previous School (probably the least consistent column in this data because of the way it is presented in the original data; we did make an attempt to move high schools listed in this column to the high school field)

We have *not* completely standardized the following data, at least not yet:

* High School
* Hometown
* Previous School

We welcome comments, corrections and questions. Please use [this repository's Issues](https://github.com/Sports-Roster-Data/womens-volleyball/issues) to let us know about any errors or omissions, or submit a pull request with any changes.
